# Benchmarks

This page compares maxminddb-rust with the other Python readers for MaxMind DB
files. Results depend on the database, the lookup pattern, and the hardware.
To measure your own workload, run
[`benchmarks/compare_readers.py`](#run-the-benchmarks).

## Summary

- Compared with the C extension of the official `maxminddb` package,
  maxminddb-rust is 1.7x faster for full-record lookups, 2.2x faster for
  iteration, and 12.6x faster for one field with `get_path()`, on GeoLite2
  City.
- The pure Python reader in the official package is about 10 times slower than
  its C extension. The default mode uses it only if the C extension is not
  available.
- maxminddb-zig returns read-only mappings and string networks. Its `query()`
  lookups cache decoded records, and its iterator reuses record objects. When
  many lookups share a record, as in GeoLite2 Country, and when iterating, it
  can be faster than maxminddb-rust. maxminddb-rust returns a new `dict` for
  each record and `ipaddress` network objects, as `maxminddb` does.

## Readers

| Package                                                       | Version | Implementation                          |
| ------------------------------------------------------------- | ------- | --------------------------------------- |
| [maxminddb-rust](https://pypi.org/project/maxminddb-rust/)    | 0.9.0   | Rust                                    |
| [maxminddb](https://pypi.org/project/maxminddb/), C extension | 3.2.0   | C (`MODE_MMAP_EXT`)                     |
| [maxminddb](https://pypi.org/project/maxminddb/), pure Python | 3.2.0   | Python (`MODE_MMAP`)                    |
| [maxminddb-zig](https://pypi.org/project/maxminddb-zig/)      | 0.1.0   | Zig, with `lookup()` and with `query()` |

The default mode, `MODE_AUTO`, uses the C extension when it is available. The
tables label the readers as follows: `rust`, `official-c`, `official-py`, `zig`,
and `zig-query`. The value in parentheses is the speed relative to the
official C extension.

## Results

### GeoLite2 City, one thread

| Case     |               rust | official-c |    official-py |               zig |         zig-query |
| -------- | -----------------: | ---------: | -------------: | ----------------: | ----------------: |
| get      |    555,240 (1.71x) |    325,601 | 28,970 (0.09x) |   305,364 (0.94x) |   441,289 (1.36x) |
| get_path | 4,040,362 (12.56x) |    321,567 | 28,382 (0.09x) |   696,252 (2.17x) | 1,020,913 (3.17x) |
| iterate  |    624,090 (2.17x) |    287,401 | 27,127 (0.09x) | 1,180,510 (4.11x) |                 - |

### GeoLite2 Country, one thread

| Case     |               rust | official-c |    official-py |               zig |         zig-query |
| -------- | -----------------: | ---------: | -------------: | ----------------: | ----------------: |
| get      |  1,180,475 (2.30x) |    513,148 | 44,488 (0.09x) |   472,697 (0.92x) | 2,070,480 (4.03x) |
| get_path | 5,504,446 (10.99x) |    501,046 | 44,832 (0.09x) |   880,541 (1.76x) | 2,890,290 (5.77x) |
| iterate  |  1,078,390 (2.54x) |    425,302 | 45,842 (0.11x) | 2,771,110 (6.52x) |                 - |

### Threads on free-threaded Python 3.14t

All threads share one reader and look up GeoLite2 City records. The values are
lookups per second across all threads.

| Threads |        `get` rust | `get` official-c |    `get_path` rust | `get_path` official-c |
| ------: | ----------------: | ---------------: | -----------------: | --------------------: |
|       1 |   485,076 (1.62x) |          300,089 | 3,716,339 (12.80x) |               290,284 |
|       2 |   898,343 (1.61x) |          558,208 |  5,158,257 (9.25x) |               557,765 |
|       4 | 1,694,272 (1.55x) |        1,094,045 |  8,453,696 (7.93x) |             1,065,534 |
|       8 | 3,017,043 (1.48x) |        2,040,533 |  8,443,275 (4.39x) |             1,923,663 |

maxminddb-zig 0.1.0 is not included. It ends with an illegal instruction
(SIGILL) when it opens a database on free-threaded Python 3.14t.

On free-threaded Python, bind a method once per thread, for example
`get = reader.get`. Looking up `reader.get` on every call creates a bound
method that changes the reference count of the shared reader, and the threads
then contend on it.

## What each case measures

- **`get`**: decode the full record for an address. maxminddb-rust and
  `maxminddb` return a `dict`. maxminddb-zig `lookup()` returns a read-only
  mapping and the network as a string. maxminddb-zig `query()` also caches
  each decoded record and returns the same object for later lookups that find
  the same record.
- **`get_path`**: read `country.iso_code`, the fastest way each reader offers
  that returns Python values. maxminddb-rust uses `get_path()`, which decodes
  only that value. maxminddb-zig decodes only the top-level `country` map.
  `maxminddb` has no partial lookup, so it decodes the full record and the
  benchmark then indexes it. maxminddb-zig `json()` is not included, because it
  returns JSON text.
- **`iterate`**: read the first 200,000 networks from the reader's iterator.
  maxminddb-rust and `maxminddb` yield `ipaddress` network objects and a new
  `dict` for each record. maxminddb-zig yields networks as strings and reuses
  the record object for networks that share a record.

## Method

- **Addresses**: 200,000 random IPv4 addresses that the database contains,
  from a fixed seed.
- **Isolation**: each reader and case runs in a new Python process. Each round
  runs every reader once, so slow changes in the machine affect all readers.
- **Values**: each sample is the best of 3 passes over the addresses, after one
  warm-up pass. Each value is the median of 5 samples, or 3 for the thread
  results.
- **Databases**: GeoLite2 City and GeoLite2 Country, built on November 6, 2026.
- **Machine**: Intel Core Ultra 7 265K, Ubuntu 26.10, Linux 7.3, `powersave`
  CPU frequency governor.
- **Python**: CPython 3.14.8, and free-threaded CPython 3.14.3 for the thread
  results.

## Run the benchmarks

Install the readers in the same environment as maxminddb-rust, then pass a
database:

```bash
uv run --with maxminddb --with maxminddb-zig \
  python benchmarks/compare_readers.py --file /path/to/GeoLite2-City.mmdb
```

Options:

- `--lib` and `--case` limit the run to some readers and cases.
- `--threads N` splits the addresses across `N` threads that share one reader.
  It leaves out the `iterate` case.
- `--json-output FILE` writes the median values to a file.

If a reader is not installed, the script stops with an error. Use `--lib` to
leave that reader out. On free-threaded Python, leave out `zig` and `zig-query`,
because maxminddb-zig 0.1.0 crashes there.

## Readers not included

- **[geoip2](https://pypi.org/project/geoip2/)**: its database reader reads
  through `maxminddb` and adds model objects, so its speed follows the
  `maxminddb` results.
- **[pandas-maxminddb](https://pypi.org/project/pandas-maxminddb/)**: the last
  release is from 2022 and has wheels only for Python 3.10 and earlier. Its API
  works on pandas columns, not single addresses.
