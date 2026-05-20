import json
import subprocess
from fractions import Fraction

import mutagen.mp4


AAC_SAMPLES_PER_FRAME = 1024
ITUNSMPB_KEY = "----:com.apple.iTunes:iTunSMPB"


def subprocess_json(sp_args):
    sp = subprocess.run(sp_args, capture_output=True, check=True)
    return json.loads(sp.stdout)


def get_first_audio_stream(path):
    data = subprocess_json(
        [
            "ffprobe",
            "-hide_banner",
            "-loglevel",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name,profile,sample_rate,time_base,duration_ts,duration",
            "-of",
            "json",
            path,
        ]
    )
    try:
        return data["streams"][0]
    except (KeyError, IndexError):
        raise Exception(f"could not find an audio stream in {path}")


def get_stream_sample_count(stream):
    sample_rate = int(stream["sample_rate"])
    duration_ts = stream.get("duration_ts")
    if duration_ts not in (None, "N/A"):
        time_base = Fraction(stream.get("time_base") or f"1/{sample_rate}")
        samples = Fraction(int(duration_ts)) * time_base * sample_rate
        return round(samples), sample_rate
    duration = stream.get("duration")
    if duration in (None, "N/A"):
        raise Exception("audio stream does not include duration information")
    return round(Fraction(duration) * sample_rate), sample_rate


def get_source_sample_count(source_file, output_sample_rate):
    source_samples, source_sample_rate = get_stream_sample_count(
        get_first_audio_stream(source_file)
    )
    if source_sample_rate == output_sample_rate:
        return source_samples
    return round(Fraction(source_samples * output_sample_rate, source_sample_rate))


def get_aac_packet_info(target_file):
    data = subprocess_json(
        [
            "ffprobe",
            "-hide_banner",
            "-loglevel",
            "error",
            "-count_packets",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name,profile,sample_rate,nb_read_packets,nb_frames",
            "-of",
            "json",
            target_file,
        ]
    )
    try:
        stream = data["streams"][0]
    except (KeyError, IndexError):
        raise Exception(f"could not find an AAC stream in {target_file}")
    if stream.get("codec_name") != "aac":
        raise Exception(f"expected AAC output, got {stream.get('codec_name')}")
    packet_count = int(stream.get("nb_read_packets") or stream.get("nb_frames") or 0)
    if packet_count <= 0:
        raise Exception(f"could not count AAC packets in {target_file}")
    return int(stream["sample_rate"]), packet_count


def get_aac_priming_samples(target_file):
    data = subprocess_json(
        [
            "ffprobe",
            "-hide_banner",
            "-loglevel",
            "error",
            "-read_intervals",
            "%+#1",
            "-select_streams",
            "a:0",
            "-show_packets",
            "-show_entries",
            "packet=side_data_list",
            "-of",
            "json",
            target_file,
        ]
    )
    packets = data.get("packets") or []
    if not packets:
        return 0
    for side_data in packets[0].get("side_data_list") or []:
        if side_data.get("side_data_type") == "Skip Samples":
            return int(side_data.get("skip_samples") or 0)
    return 0


def build_itunsmpb_value(priming, padding, sample_count):
    fields = [
        "00000000",
        f"{priming:08X}",
        f"{padding:08X}",
        f"{sample_count:016X}",
    ]
    fields.extend(["00000000"] * 8)
    return " " + " ".join(fields)


def write_aac_gapless_metadata(source_file, target_file, debug=False):
    output_sample_rate, packet_count = get_aac_packet_info(target_file)
    priming = get_aac_priming_samples(target_file)
    source_samples = get_source_sample_count(source_file, output_sample_rate)
    encoded_samples = packet_count * AAC_SAMPLES_PER_FRAME
    padding = encoded_samples - priming - source_samples
    if padding < 0:
        raise Exception(
            "could not compute AAC gapless metadata: "
            f"{encoded_samples=} {priming=} {source_samples=}"
        )
    itunsmpb = build_itunsmpb_value(priming, padding, source_samples)
    if debug:
        print(f"iTunSMPB {target_file}: {itunsmpb}")
    mp4 = mutagen.mp4.MP4(target_file)
    mp4[ITUNSMPB_KEY] = [mutagen.mp4.MP4FreeForm(itunsmpb.encode("ascii"))]
    mp4.save()
