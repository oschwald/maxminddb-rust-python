#!/usr/bin/python

import argparse
import random
import socket
import struct
import timeit

import maxminddb_rust


def generate_ips(count):
    random.seed(0)
    return [
        socket.inet_ntoa(struct.pack("!L", random.getrandbits(32)))
        for _ in range(count)
    ]


parser = argparse.ArgumentParser(description="Benchmark maxminddb batch lookups.")
parser.add_argument("--count", default=250000, type=int, help="number of lookups")
parser.add_argument("--batch-size", default=100, type=int, help="batch size")
parser.add_argument("--file", default="GeoIP2-City.mmdb", help="path to mmdb file")

args = parser.parse_args()
if args.batch_size <= 0:
    raise ValueError("--batch-size must be positive")

reader = maxminddb_rust.open_database(args.file)
ips = generate_ips(args.count)
batches = [
    ips[start : start + args.batch_size]
    for start in range(0, len(ips), args.batch_size)
]


def lookup_batches() -> None:
    for batch in batches:
        reader.get_many(batch)


elapsed = timeit.timeit(
    lookup_batches,
    number=1,
)

total_lookups = len(ips)
print(f"{int(total_lookups / elapsed):,}", "lookups per second (batch mode)")
