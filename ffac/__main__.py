#!/usr/bin/env python
# -*- coding: utf-8 -*-
import sys
import os
import io
import argparse
import subprocess
import shutil
import base64
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
import tqdm
import mutagen.mp4
import mutagen.flac
import mutagen.oggvorbis
import mutagen.oggopus
import mutagen
import PIL.Image

from ffac.gapless import write_aac_gapless_metadata

FOLDER_ENDINGS = ("/", "\\")
BAD_FILE_STARTS = (".", "~", "_")


def generate_folder_name(args):
    folder = args.source
    while folder.endswith(FOLDER_ENDINGS):
        folder = folder[:-1]
    folder += f" [{args.output_format.upper()}]"
    return folder


mode_to_bpp = {
    "1": 1,
    "L": 8,
    "P": 8,
    "RGB": 24,
    "RGBA": 32,
    "CMYK": 32,
    "YCbCr": 24,
    "I": 32,
    "F": 32,
}


def picture_from_file(path, ext_bytes=None):
    ext = get_extension(path)
    if ext_bytes:
        data = ext_bytes
    else:
        with open(path, "rb") as h:
            data = h.read()

    picture = mutagen.flac.Picture()
    picture.data = data
    picture.type = 3
    picture.desc = "Front cover"
    picture.mime = f"image/{ext}"
    if ext_bytes:
        img = PIL.Image.open(io.BytesIO(data))
    else:
        img = PIL.Image.open(path)
    picture.width = img.width
    picture.height = img.height
    picture.depth = mode_to_bpp[img.mode]
    return picture


def add_picture_to_ogg(filename, picture):
    file_ = (
        mutagen.oggvorbis.OggVorbis(filename)
        if filename.endswith((".ogg", ".oga"))
        else mutagen.oggopus.OggOpus(filename)
    )
    picture_data = picture.write()
    encoded_data = base64.b64encode(picture_data)
    vcomment_value = encoded_data.decode("ascii")
    file_["metadata_block_picture"] = [vcomment_value]
    file_.save()


def search_for_picture(dirname):
    for bn in ("cover", "folder"):
        for ext in ("jpg", "jpeg", "png"):
            path = os.path.join(dirname, f"{bn}.{ext}")
            if os.path.isfile(path):
                return picture_from_file(path)


def transfer_image(args, source_file, target_file):
    picture = None
    if source_file.endswith(".flac"):
        fl = mutagen.flac.FLAC(source_file)
        if fl.pictures:
            picture = fl.pictures[0]
    elif source_file.endswith(".m4a"):
        m = mutagen.mp4.MP4(source_file)
        if m.get("covr"):
            cover = m["covr"][0]
            fake_filename = "cover.{}".format("png" if cover.imageformat == 14 else "jpeg")
            picture = picture_from_file(fake_filename, ext_bytes=bytes(cover))
    if not picture:
        picture = search_for_picture(os.path.dirname(source_file))
    if picture and target_file.endswith((".ogg", ".oga", ".opus")):
        add_picture_to_ogg(target_file, picture)
    if target_file.endswith(".oga") and args.output_format == "ogg":
        shutil.move(target_file, target_file[:-1] + "g")


def process_file(args, source_file, target_file):
    sp_kwargs = {}
    sp_kwargs["capture_output"] = True
    sp_kwargs["check"] = True
    sp_args = build_subprocess_args(args, source_file, target_file)
    try:
        subprocess.run(sp_args, **sp_kwargs)
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode("utf8", errors="replace")
        if "error: cannot decode" in stderr:
            pass
        else:
            raise Exception(f"called process failed, stderr: {stderr}")
    if args.output_format in ("aac", "m4a"):
        write_aac_gapless_metadata(
            source_file, target_file, debug=getattr(args, "debug", False)
        )
    if args.output_format in ("ogg", "oga", "opus"):
        transfer_image(args, source_file, target_file)


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
    if args.quality is None and args.bitrate is None:
        args.quality = {"mp3": "2", "aac": "4", "m4a": "3", "ogg": "5", "oga": "5"}.get(
            args.output_format
        )
    if args.bitrate is None and args.output_format == "opus":
        args.bitrate = "160K"


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
    if of == "alac":
        return ["-c:a", "alac"]
    return []


def build_subprocess_args(args, source_file, target_file):
    result = ["ffmpeg", "-i", source_file]
    if not args.no_images:
        result.extend(["-c:v", "copy"])
    result.extend(get_codec_args(args))
    if args.bitrate:
        result.extend(["-b:a", args.bitrate])
    elif args.quality:
        result.extend(["-q:a", args.quality])
    if args.resample:
        result.extend(["-af", f"aresample={args.resample}"])
    if args.output_format == "alac":
        result.extend(["-movflags", "+faststart"])
    result.append(target_file)
    return result


def get_extension(filepath):
    return os.path.splitext(filepath)[1][1:]


def get_target_extension(args):
    return "." + (
        {"aac": "m4a", "ogg": "oga", "alac": "m4a"}.get(args.output_format) or args.output_format
    )


def check_exists(target_file):
    if target_file.endswith((".oga", ".ogg")):
        basename = os.path.splitext(target_file)[0]
        return os.path.exists(f"{basename}.oga") or os.path.exists(f"{basename}.ogg")
    else:
        return os.path.exists(target_file)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
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
    parser.add_argument("--quality", "-q", help="format-specific quality setting (q:a)")
    parser.add_argument(
        "--bitrate", "-b", help="output bitrate. Incompatible with --quality"
    )
    parser.add_argument("--no-images", "-ni", help="suppress embedding images")
    parser.add_argument("--processes", "-p", help="number of processes to use")
    parser.add_argument("--resample", "-r", help="resample to specified sample rate")
    args = parser.parse_args()
    load_defaults(args)

    args.source = os.path.abspath(args.source)
    while args.source.endswith(FOLDER_ENDINGS):
        args.source = args.source[:-1]
    output_root_folder = generate_folder_name(args)
    if os.path.isfile(output_root_folder):
        print("Please delete the file {}".format(output_root_folder))
        sys.exit(1)
    executor = ThreadPoolExecutor(max_workers=args.processes)
    futures = []
    single_file_mode = os.path.isfile(args.source)
    message = f"converting {args.source} to {args.output_format}"
    if not single_file_mode:
        message += f" using {args.processes} concurrent workers..."
    print(message)
    if single_file_mode:
        target_file = os.path.splitext(args.source)[0] + get_target_extension(args)
        process_file(args, args.source, target_file)
        sys.exit(0)
    for (root, _, files) in os.walk(args.source):
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
            source_file = os.path.join(root, f)
            target_file = os.path.join(
                root, os.path.splitext(f)[0] + target_extension
            ).replace(args.source, output_root_folder, 1)
            if check_exists(target_file):
                continue
            try:
                os.makedirs(os.path.dirname(target_file))
            except FileExistsError:
                pass
            futures.append(executor.submit(process_file, args, source_file, target_file))
        for f in to_copy:
            target_dir = root.replace(args.source, output_root_folder, 1)
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
