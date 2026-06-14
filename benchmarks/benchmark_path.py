#!/usr/bin/python

import argparse
import random
import socket
import struct
import timeit

import maxminddb_rust

parser = argparse.ArgumentParser(description="Benchmark maxminddb get vs get_path.")
parser.add_argument("--count", default=250000, type=int, help="number of lookups")
parser.add_argument("--batch-size", default=100, type=int, help="batch size")
parser.add_argument(
    "--file", default="/var/lib/GeoIP/GeoLite2-City.mmdb", help="path to mmdb file"
)

args = parser.parse_args()
if args.batch_size <= 0:
    raise ValueError("--batch-size must be positive")

random.seed(0)
reader = maxminddb_rust.open_database(args.file)

# Pre-generate IPs to ensure fair comparison (though random lookup overhead is small)
ips = [
    socket.inet_ntoa(struct.pack("!L", random.getrandbits(32)))
    for _ in range(args.count)
]
batches = [
    ips[start : start + args.batch_size]
    for start in range(0, len(ips), args.batch_size)
]


def lookup_full():
    for ip in ips:
        try:
            res = reader.get(ip)
            if res:
                res.get("country", {}).get("iso_code")
        except ValueError:
            pass


def lookup_path():
    path = ("country", "iso_code")
    for ip in ips:
        try:
            reader.get_path(ip, path)
        except ValueError:
            pass


def lookup_many_path():
    path = ("country", "iso_code")
    for batch in batches:
        try:
            reader.get_many_path(batch, path)
        except ValueError:
            pass


print(f"Benchmarking with {args.count:,} lookups...")

time_full = timeit.timeit(lookup_full, number=1)
print(f"Full record decode: {int(args.count / time_full):,} lookups per second")

time_path = timeit.timeit(lookup_path, number=1)
print(f"Path decode (get_path): {int(args.count / time_path):,} lookups per second")

time_many_path = timeit.timeit(lookup_many_path, number=1)
print(
    f"Batch path decode (get_many_path): "
    f"{int(args.count / time_many_path):,} lookups per second"
)

print(f"Speedup: {time_full / time_path:.2f}x")
print(f"Batch path speedup vs get_path: {time_path / time_many_path:.2f}x")
