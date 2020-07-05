#!/usr/bin/env python
# -*- coding: utf-8 -*-
import sys
import os
import argparse
import subprocess
import shutil
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
import tqdm


FOLDER_ENDINGS = ("/", "\\")
BAD_FILE_STARTS = (".", "~", "_")


def generate_folder_name(args):
    folder = args.folder
    while folder.endswith(FOLDER_ENDINGS):
        folder = folder[:-1]
    folder += f" [{args.output_format.upper()}]"
    return folder


def call_wrapper(*args, **kwargs):
    kwargs["capture_output"] = True
    kwargs["check"] = True
    try:
        return subprocess.run(*args, **kwargs)
    except subprocess.CalledProcessError as e:
        raise Exception(f"ffmpeg failed, stderr: {e.stderr}")


def load_config(args, json_path):
    with open(json_path) as f:
        json_map = json.load(f)
    for key in json_map:
        if getattr(args, key) is None:
            setattr(args, key, json_map[key])


def load_defaults(args):
    default_config_path = os.path.join(os.path.expanduser("~"), ".ffac.json")
    if args.config:
        load_config(args, args.config)
    elif os.path.isfile(default_config_path):
        load_config(args, default_config_path)
    if args.input_formats is None:
        args.input_formats = "flac"
    args.input_formats = set(args.input_formats.split(","))
    if args.output_format is None:
        args.output_format = "mp3"
    if not args.copy_formats:
        args.copy_formats = set()
    else:
        args.copy_formats = set(args.copy_formats.split(","))
    if args.processes is None:
        args.processes = os.cpu_count()
    else:
        args.processes = int(args.processes)
    while args.output_format.startswith("."):
        args.output_format = args.output_format[1:]
    if args.quality is None:
        args.quality = {
            "mp3": "2",
            "aac": "3",
            "m4a": "3",
            "ogg": "6",
            "oga": "6",
        }.get(args.output_format)
    if args.bitrate is None and args.output_format == "opus":
        args.bitrate = "180K"


def get_codec_args(args):
    of = args.output_format
    if of == "mp3":
        return ["-c:a", "libmp3lame"]
    if of in ("m4a", "aac"):
        codec = "aac_at" if sys.platform == "darwin" else "aac"
        return ["-c:a", codec]
    if of in ("ogg", "oga"):
        return ["-c:a", "libvorbis"]
    if of == "opus":
        return ["-c:a", "libopus"]
    return []


def build_subprocess_args(args, source_file, target_file):
    result = ["ffmpeg", "-i", source_file]
    if not args.no_images:
        result.extend(["-c:v", "copy"])
    result.extend(get_codec_args(args))
    if args.quality:
        result.extend(["-q:a", args.quality])
    elif args.bitrate:
        result.extend(["-b:a", args.bitrate])
    if args.resample:
        result.extend(["-af", f"aresample={args.bitrate}"])
    result.append(target_file)
    return result


def get_extension(filepath):
    return os.path.splitext(filepath)[1][1:]


def get_target_extension(args):
    return "." + (
        {"aac": "m4a", "ogg": "oga"}.get(args.output_format)
        or args.output_format
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("folder")
    parser.add_argument(
        "--input_formats", "-i", help="comma-separated input extensions list"
    )
    parser.add_argument("--output_format", "-o", help="output file format")
    parser.add_argument("--config", "-c", help="external config file path")
    parser.add_argument(
        "--copy_formats",
        "-cf",
        help="comma-separated list of file extensions to be copied without processing",
    )
    parser.add_argument(
        "--quality", "-q", help="format-specific quality setting (q:a)"
    )
    parser.add_argument(
        "--bitrate", "-b", help="output bitrate. Incompatible with --quality"
    )
    parser.add_argument("--no-images", "-ni", help="suppress embedding images")
    parser.add_argument("--processes", "-p", help="number of processes to use")
    parser.add_argument(
        "--resample", "-r", help="resample to specified sample rate"
    )
    args = parser.parse_args()
    load_defaults(args)

    args.folder = os.path.abspath(args.folder)
    while args.folder.endswith(FOLDER_ENDINGS):
        args.folder = args.folder[:-1]
    output_root_folder = generate_folder_name(args)
    if os.path.isfile(output_root_folder):
        print("Please delete the file {}".format(output_root_folder))
        sys.exit(1)
    executor = ProcessPoolExecutor(max_workers=args.processes)
    futures = []
    single_file_mode = os.path.isfile(args.folder)
    message = f"converting {args.folder} to {args.output_format}"
    if not single_file_mode:
        message += f" using {args.processes} concurrent processes..."
    print(message)
    if single_file_mode:
        target_name = os.path.splitext(args.folder)[0] + get_target_extension(
            args
        )
        subprocess_args = build_subprocess_args(
            args, args.folder, target_name
        )
        call_wrapper(subprocess_args)
        sys.exit(0)
    for (root, _, files) in os.walk(args.folder):
        to_convert = [
            x
            for x in files
            if get_extension(x) in args.input_formats
            and not x.startswith(BAD_FILE_STARTS)
        ]
        to_copy = [x for x in files if get_extension(x) in args.copy_formats]
        if not to_convert and not to_copy:
            continue
        for f in to_convert:
            target_extension = get_target_extension(args)
            target_name = os.path.join(
                root, os.path.splitext(f)[0] + target_extension
            ).replace(args.folder, output_root_folder, 1)
            if os.path.exists(target_name):
                continue
            try:
                os.makedirs(os.path.dirname(target_name))
            except FileExistsError:
                pass
            subprocess_args = build_subprocess_args(
                args, os.path.join(root, f), target_name
            )
            futures.append(executor.submit(call_wrapper, subprocess_args))
        for f in to_copy:
            target_dir = root.replace(args.folder, output_root_folder, 1)
            try:
                os.makedirs(target_dir)
            except FileExistsError:
                pass
            futures.append(
                executor.submit(shutil.copy, os.path.join(root, f), target_dir)
            )
    for future in tqdm.tqdm(as_completed(futures)):
        future.result()
    print(f"finished processing, result is in {output_root_folder}")


if __name__ == "__main__":
    main()
