"""
Type stubs for maxminddb_rust.

This module provides a high-performance alternative to the maxminddb Python package,
implemented in Rust using PyO3 with 100% API compatibility.
"""

from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network
from os import PathLike
from types import TracebackType
from typing import Any, Iterator, Literal, Optional, Sequence, Union

__all__ = [
    "Reader",
    "Metadata",
    "InvalidDatabaseError",
    "open_database",
    "MODE_AUTO",
    "MODE_MMAP_EXT",
    "MODE_MMAP",
    "MODE_FILE",
    "MODE_MEMORY",
    "MODE_FD",
]

# Mode constants
MODE_AUTO: Literal[0]
MODE_MMAP_EXT: Literal[1]
MODE_MMAP: Literal[2]
MODE_FILE: Literal[4]
MODE_MEMORY: Literal[8]
MODE_FD: Literal[16]

class InvalidDatabaseError(RuntimeError):
    """Exception raised when the MaxMind DB file is invalid or corrupt."""

    ...

class Metadata:
    """Metadata about a MaxMind DB database."""

    binary_format_major_version: int
    """The major version number of the binary format used when creating the database."""

    binary_format_minor_version: int
    """The minor version number of the binary format used when creating the database."""

    build_epoch: int
    """The Unix epoch timestamp for when the database was built."""

    database_type: str
    """A string identifying the database type (e.g., 'GeoIP2-City', 'GeoLite2-Country')."""

    ip_version: int
    """The IP version of the data in a database. A value of 4 means IPv4 only; 6 supports both IPv4 and IPv6."""

    node_count: int
    """The number of nodes in the search tree."""

    record_size: int
    """The record size in bits (24, 28, or 32)."""

    @property
    def description(self) -> dict[str, str]:
        """A dictionary from locale codes to the database description in that locale."""
        ...

    @property
    def languages(self) -> list[str]:
        """A list of locale codes supported by the database for descriptions and other text."""
        ...

    @property
    def node_byte_size(self) -> int:
        """The size of a node in bytes."""
        ...

    @property
    def search_tree_size(self) -> int:
        """The size of the search tree in bytes."""
        ...

class Reader:
    """
    A MaxMind DB database reader.

    Provides methods to query IP address information from MaxMind DB files.
    Supports both memory-mapped files (MODE_MMAP) and in-memory (MODE_MEMORY) modes.
    """

    def __init__(
        self, database: Union[str, PathLike[str]], mode: int = MODE_AUTO
    ) -> None:
        """
        Initialize a Reader for a MaxMind DB file.

        Args:
            database: Path to the MaxMind DB file.
            mode: The mode to use when opening the database. Defaults to MODE_AUTO.

        Raises:
            FileNotFoundError: If the database file does not exist.
            IOError: If the database file cannot be read or memory-mapped.
            InvalidDatabaseError: If the file is not a valid MaxMind DB file.
            ValueError: If an unsupported mode is specified.
        """
        ...

    @property
    def closed(self) -> bool:
        """True if the database has been closed, False otherwise."""
        ...

    def get(
        self, ip_address: Union[str, IPv4Address, IPv6Address]
    ) -> Optional[dict[str, Any]]:
        """
        Query the database for information about an IP address.

        Args:
            ip_address: The IP address to look up. May be a string (e.g., '1.2.3.4')
                or an ipaddress.IPv4Address or ipaddress.IPv6Address object.

        Returns:
            A dictionary containing the database record for the IP address, or None
            if the address is not in the database.

        Raises:
            ValueError: If the database has been closed or the IP address is invalid.
            InvalidDatabaseError: If the database data is corrupt or invalid.
        """
        ...

    def get_with_prefix_len(
        self, ip_address: Union[str, IPv4Address, IPv6Address]
    ) -> tuple[Optional[dict[str, Any]], int]:
        """
        Query the database for information about an IP address and return the network prefix length.

        Args:
            ip_address: The IP address to look up. May be a string (e.g., '1.2.3.4')
                or an ipaddress.IPv4Address or ipaddress.IPv6Address object.

        Returns:
            A tuple of (record, prefix_length) where record is a dictionary containing
            the database record (or None if not found), and prefix_length is an integer
            representing the network prefix length associated with the record.

        Raises:
            ValueError: If the database has been closed or the IP address is invalid.
            InvalidDatabaseError: If the database data is corrupt or invalid.
        """
        ...

    def get_path(
        self,
        ip_address: Union[str, IPv4Address, IPv6Address],
        path: Sequence[Union[str, int]],
    ) -> Optional[Any]:
        """
        Query the database for a specific path within the record.

        This method is more efficient than get() when you only need a specific field
        (e.g., country code) from the record, as it avoids decoding the entire record.

        Args:
            ip_address: The IP address to look up.
            path: A sequence (tuple or list) of strings or integers representing the path to the data.

        Returns:
            The value at the specified path, or None if the IP address or path is not found.

        Example:
            >>> reader.get_path('8.8.8.8', ('country', 'iso_code'))
            'US'
            >>> reader.get_path('8.8.8.8', ('subdivisions', 0, 'iso_code'))
            'CA'
        """
        ...

    def get_many(self, ips: list[str]) -> list[Optional[dict[str, Any]]]:
        """
        Query the database for multiple IP addresses in a single batch operation.

        This is an extension method not available in the original maxminddb module.
        It provides better performance than calling get() repeatedly by reducing
        call overhead and releasing the GIL during the entire batch operation.

        Args:
            ips: A list of IP address strings to look up (e.g., ['1.2.3.4', '8.8.8.8']).

        Returns:
            A list of dictionaries containing database records for each IP address.
            Elements will be None for IP addresses not found in the database.
            The order of results matches the order of input IPs.

        Raises:
            ValueError: If the database has been closed or any IP address is invalid.
            InvalidDatabaseError: If the database data is corrupt or invalid.
        """
        ...

    def metadata(self) -> Metadata:
        """
        Get metadata about the MaxMind DB database.

        Returns:
            A Metadata object containing information about the database.

        Raises:
            OSError: If the database has been closed.
        """
        ...

    def close(self) -> None:
        """
        Close the database and release resources.

        Closes the MaxMind DB file handle and releases associated resources.
        After calling this method, attempting to call get() or other query
        methods will raise a ValueError.
        """
        ...

    def __enter__(self) -> Reader:
        """
        Enter the context manager (for use with 'with' statement).

        Returns:
            The Reader object itself.

        Raises:
            ValueError: If attempting to reopen a closed database.
        """
        ...

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        """
        Exit the context manager (for use with 'with' statement).

        Automatically closes the database when exiting the 'with' block.
        """
        ...

    def __iter__(
        self,
    ) -> Iterator[tuple[Union[IPv4Network, IPv6Network], dict[str, Any]]]:
        """
        Iterate over all networks in the database.

        Returns an iterator that yields (network, data) tuples for all networks
        in the database. Networks are represented as ipaddress.IPv4Network or
        ipaddress.IPv6Network objects.

        Yields:
            Tuples of (network, record) for each entry in the database.

        Raises:
            ValueError: If the database has been closed.
        """
        ...

def open_database(database: Union[str, PathLike[str]], mode: int = MODE_AUTO) -> Reader:
    """
    Open a MaxMind DB database file.

    Args:
        database: Path to the MaxMind DB file. Can be a string or PathLike object.
        mode: The mode to use when opening the database. Defaults to MODE_AUTO.
            Available modes:
            - MODE_AUTO (0): Automatically choose the best mode (uses MODE_MMAP)
            - MODE_MMAP (2): Use memory-mapped file I/O (default, best performance)
            - MODE_MMAP_EXT (1): Same as MODE_MMAP
            - MODE_MEMORY (8): Load entire database into memory
            - MODE_FILE (4): Not yet supported
            - MODE_FD (16): Not yet supported

    Returns:
        A Reader object that can be used to query the database.

    Raises:
        FileNotFoundError: If the database file does not exist.
        IOError: If the database file cannot be read or memory-mapped.
        InvalidDatabaseError: If the file is not a valid MaxMind DB file.
        ValueError: If an unsupported mode is specified.

    Example:
        >>> import maxminddb_rust
        >>> reader = maxminddb_rust.open_database('/path/to/GeoIP2-City.mmdb')
        >>> reader.get('8.8.8.8')
        {'city': {'names': {'en': 'Mountain View'}}, ...}
        >>> reader.close()

        >>> # Using context manager
        >>> with maxminddb_rust.open_database('/path/to/GeoIP2-City.mmdb') as reader:
        ...     result = reader.get('8.8.8.8')

        >>> # Specify mode explicitly
        >>> reader = maxminddb_rust.open_database('/path/to/GeoIP2-City.mmdb',
        ...                                       mode=maxminddb_rust.MODE_MEMORY)
    """
    ...
