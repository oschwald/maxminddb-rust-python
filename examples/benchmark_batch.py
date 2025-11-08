#!/usr/bin/python

import argparse
import random
import socket
import struct
import timeit

import maxminddb

parser = argparse.ArgumentParser(description="Benchmark maxminddb batch lookups.")
parser.add_argument("--count", default=250000, type=int, help="number of lookups")
parser.add_argument("--batch-size", default=100, type=int, help="batch size")
parser.add_argument("--file", default="GeoIP2-City.mmdb", help="path to mmdb file")

args = parser.parse_args()

random.seed(0)
reader = maxminddb.open_database(args.file)

# Pre-generate IPs
ips = [
    socket.inet_ntoa(struct.pack("!L", random.getrandbits(32)))
    for _ in range(args.batch_size)
]


def lookup_batch() -> None:
    reader.get_many(ips)


num_batches = args.count // args.batch_size
elapsed = timeit.timeit(
    "lookup_batch()",
    setup="from __main__ import lookup_batch",
    number=num_batches,
)

total_lookups = num_batches * args.batch_size
print(f"{int(total_lookups / elapsed):,}", "lookups per second (batch mode)")
