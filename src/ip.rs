use pyo3::{
    exceptions::{PyAttributeError, PyOverflowError, PyTypeError, PyValueError},
    prelude::*,
    sync::PyOnceLock,
    types::{PyInt, PyString},
};
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr};

static IPADDRESS_TYPES: PyOnceLock<IpaddressTypes> = PyOnceLock::new();

/// The `ipaddress` address classes, held for fast identity checks. They are
/// fetched once per process. If a test has replaced a class at that point,
/// even with a callable that is not a type, objects of the real class miss
/// the identity check and take the name check in `parse_ip_address`.
pub(crate) struct IpaddressTypes {
    pub(crate) ipv4_address: Py<PyAny>,
    pub(crate) ipv6_address: Py<PyAny>,
}

impl IpaddressTypes {
    pub(crate) fn get(py: Python<'_>) -> PyResult<&'static Self> {
        if let Some(types) = IPADDRESS_TYPES.get(py) {
            return Ok(types);
        }
        // Import outside the lock. The import can run Python code, and code
        // that parses an IP address on this thread would re-enter the lock.
        let ipaddress = py.import("ipaddress")?;
        let types = Self {
            ipv4_address: ipaddress.getattr("IPv4Address")?.unbind(),
            ipv6_address: ipaddress.getattr("IPv6Address")?.unbind(),
        };
        Ok(IPADDRESS_TYPES.get_or_init(py, || types))
    }
}

/// Parse an IP address from a string or an `ipaddress` object.
#[inline(always)]
pub(crate) fn parse_ip_address(ip_address: &Bound<'_, PyAny>) -> PyResult<IpAddr> {
    // Fast path: Try string first (most common case)
    if let Ok(py_str) = ip_address.cast::<PyString>() {
        return parse_ip_string(py_str.to_str()?);
    }

    let types = IpaddressTypes::get(ip_address.py())?;
    let type_ptr = ip_address.get_type_ptr().cast::<pyo3::ffi::PyObject>();
    if type_ptr == types.ipv4_address.as_ptr() {
        return extract_ipv4_object(ip_address);
    }
    if type_ptr == types.ipv6_address.as_ptr() {
        return extract_ipv6_object(ip_address);
    }

    // Slow path: other classes with the stdlib names, such as test doubles.
    let type_name = ip_address.get_type().name()?;
    let type_name_str = type_name.to_str()?;
    if type_name_str == "IPv4Address" {
        return extract_ipv4_object(ip_address);
    }
    if type_name_str == "IPv6Address" {
        return extract_ipv6_object(ip_address);
    }

    Err(PyTypeError::new_err(
        "argument 1 must be a string or ipaddress object",
    ))
}

#[inline(always)]
fn parse_ip_string(s: &str) -> PyResult<IpAddr> {
    if let Some(ip) = parse_ipv4_string(s.as_bytes()) {
        return Ok(IpAddr::V4(ip));
    }

    parse_ip_string_fallback(s)
}

#[inline(always)]
fn parse_ipv4_string(bytes: &[u8]) -> Option<Ipv4Addr> {
    let mut octets = [0u8; 4];
    let mut octet_index = 0;
    let mut value: u16 = 0;
    let mut digits = 0;

    for &byte in bytes {
        if byte == b'.' {
            if digits == 0 || octet_index == 3 {
                return None;
            }
            octets[octet_index] = value as u8;
            octet_index += 1;
            value = 0;
            digits = 0;
            continue;
        }

        if !byte.is_ascii_digit() {
            return None;
        }
        if digits == 1 && value == 0 {
            return None;
        }

        digits += 1;
        if digits > 3 {
            return None;
        }
        value = value * 10 + u16::from(byte - b'0');
        if value > u16::from(u8::MAX) {
            return None;
        }
    }

    if octet_index != 3 || digits == 0 {
        return None;
    }
    octets[octet_index] = value as u8;

    Some(Ipv4Addr::from(octets))
}

#[inline(always)]
fn parse_ip_string_fallback(s: &str) -> PyResult<IpAddr> {
    s.parse().map_err(|_| {
        PyValueError::new_err(format!(
            "'{}' does not appear to be an IPv4 or IPv6 address",
            s
        ))
    })
}

#[inline(always)]
fn extract_ipv4_object(ip_address: &Bound<'_, PyAny>) -> PyResult<IpAddr> {
    // The stdlib classes store their numeric address in `_ip`. Reading it
    // avoids allocating the temporary `packed` bytes used by PyO3's generic
    // IpAddr conversion. Fall back in case another implementation differs.
    let Some(value) = ip_int(ip_address)? else {
        return ip_address.extract::<IpAddr>();
    };
    match value.extract::<u32>() {
        Ok(value) => Ok(IpAddr::V4(Ipv4Addr::from(value))),
        Err(err) if is_out_of_range(ip_address.py(), &err) => ip_address.extract::<IpAddr>(),
        Err(err) => Err(err),
    }
}

#[inline(always)]
fn extract_ipv6_object(ip_address: &Bound<'_, PyAny>) -> PyResult<IpAddr> {
    let Some(value) = ip_int(ip_address)? else {
        return ip_address.extract::<IpAddr>();
    };
    match value.extract::<u128>() {
        Ok(value) => Ok(IpAddr::V6(Ipv6Addr::from(value))),
        Err(err) if is_out_of_range(ip_address.py(), &err) => ip_address.extract::<IpAddr>(),
        Err(err) => Err(err),
    }
}

/// Return the `_ip` attribute if it is an int. Return `None` if the object has
/// no `_ip` or `_ip` is not an int. A value that is not an int is not
/// converted, so no `__index__` method runs. Any other error, such as a
/// `RuntimeError` or `ValueError` raised by a property, propagates.
#[inline(always)]
fn ip_int<'py>(ip_address: &Bound<'py, PyAny>) -> PyResult<Option<Bound<'py, PyInt>>> {
    let py = ip_address.py();
    match ip_address.getattr(pyo3::intern!(py, "_ip")) {
        Ok(value) => Ok(value.cast_into::<PyInt>().ok()),
        Err(err) if err.is_instance_of::<PyAttributeError>(py) => Ok(None),
        Err(err) => Err(err),
    }
}

/// Report whether an error from converting an int `_ip` means it is out of
/// range for the address. PyO3 raises `OverflowError` or `ValueError` for an
/// out-of-range int.
fn is_out_of_range(py: Python<'_>, err: &PyErr) -> bool {
    err.is_instance_of::<PyOverflowError>(py) || err.is_instance_of::<PyValueError>(py)
}

/// Generate the error message for an IPv6 lookup in an IPv4 database.
#[inline]
pub(crate) fn ipv6_in_ipv4_error(ip: &IpAddr) -> String {
    format!(
        "Error looking up {}. You attempted to look up an IPv6 address in an IPv4-only database",
        ip
    )
}

#[cfg(test)]
mod tests {
    use super::parse_ipv4_string;
    use std::net::Ipv4Addr;

    #[test]
    fn parses_strict_ipv4_strings() {
        assert_eq!(
            parse_ipv4_string(b"0.1.2.255"),
            Some(Ipv4Addr::new(0, 1, 2, 255))
        );
        assert_eq!(
            parse_ipv4_string(b"192.0.2.1"),
            Some(Ipv4Addr::new(192, 0, 2, 1))
        );
    }

    #[test]
    fn rejects_ipv4_strings_that_std_parser_rejects() {
        for value in [
            b"01.2.3.4".as_slice(),
            b"1.02.3.4".as_slice(),
            b"1.2.3.04".as_slice(),
            b"1.2.3".as_slice(),
            b"1.2.3.4.5".as_slice(),
            b"1..2.3".as_slice(),
            b"256.1.1.1".as_slice(),
            b"1.2.3.4 ".as_slice(),
            b" 1.2.3.4".as_slice(),
            b"2001:db8::1".as_slice(),
        ] {
            assert_eq!(parse_ipv4_string(value), None);
        }
    }
}
