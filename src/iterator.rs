use crate::{
    decode::PyDecodedValue,
    maxminddb_crate::{self, MaxMindDbError, Within, WithinOptions},
    InvalidDatabaseError, ReaderSource,
};
use memmap2::Mmap;
use pyo3::{
    exceptions::{PyAttributeError, PyRuntimeError, PyTypeError, PyValueError},
    prelude::*,
    sync::PyOnceLock,
    types::{PyDict, PyList, PyTuple, PyType},
};
use self_cell::self_cell;
use std::{str::FromStr, sync::Arc};

/// Iterator for Reader that yields `(network, record)` tuples.
#[pyclass(module = "maxminddb_rust")]
pub(crate) struct ReaderIterator {
    iter: ReaderWithin,
    cache: IpaddressCache,
}

impl ReaderIterator {
    #[inline]
    fn root_network_for_ip_version(
        ip_version: u16,
    ) -> PyResult<(ipnetwork::IpNetwork, &'static str)> {
        let (network_str, network_type) = if ip_version == 4 {
            ("0.0.0.0/0", "IPv4")
        } else {
            ("::/0", "IPv6")
        };

        let network = ipnetwork::IpNetwork::from_str(network_str).map_err(|err| {
            InvalidDatabaseError::new_err(format!(
                "Failed to create {} network: {}",
                network_type, err
            ))
        })?;
        Ok((network, network_type))
    }

    pub(crate) fn new(py: Python, reader: Arc<ReaderSource>) -> PyResult<Self> {
        let ip_version = reader.metadata().ip_version;
        let (network, network_type) = Self::root_network_for_ip_version(ip_version)?;

        let iter = ReaderWithin::new(reader, network).map_err(|err| {
            InvalidDatabaseError::new_err(format!("Failed to iterate {}: {}", network_type, err))
        })?;

        let cache = IpaddressCache::new(py)?;

        Ok(Self { iter, cache })
    }
}

#[pymethods]
impl ReaderIterator {
    fn __iter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    fn __next__(&mut self, py: Python) -> PyResult<Option<(Py<PyAny>, Py<PyAny>)>> {
        let next_item = match self.iter.next() {
            Some(result) => result
                .map_err(|err| InvalidDatabaseError::new_err(format!("Iteration error: {err}")))?,
            None => return Ok(None),
        };
        let network_obj = self.cache.new_network(py, next_item.ip_net)?.unbind();
        let data_obj = next_item.data.into_py();

        Ok(Some((network_obj, data_obj)))
    }
}

/// The `ipaddress` classes and netmasks used to build networks. Each iterator
/// builds its own copy, so a class patched after an earlier iteration (for
/// example by `unittest.mock`) is used and checked again.
struct IpaddressCache {
    ipv4_network_cls: Py<PyAny>,
    ipv6_network_cls: Py<PyAny>,
    ipv4_address_cls: Py<PyAny>,
    ipv6_address_cls: Py<PyAny>,
    /// The netmask address for each IPv4 prefix length, 0 to 32.
    ipv4_netmasks: Box<[Py<PyAny>]>,
    /// The netmask address for each IPv6 prefix length, 0 to 128.
    ipv6_netmasks: Box<[Py<PyAny>]>,
    single_host_returns_iter: bool,
    /// True if a class is not a type, a class has a patched constructor, or
    /// direct construction did not match the `ipaddress` constructors.
    /// Networks are then built by the constructors.
    use_constructors: bool,
}

/// Python checks for direct construction. `uses_stdlib_constructors` reports
/// whether the classes still have the stdlib constructors. `matches` reports
/// whether a directly built network matches one built by the `ipaddress`
/// constructor: the same instance state, string, hash, and hosts.
const NETWORK_CHECK_SOURCE: &std::ffi::CStr = c"
import ipaddress

_MISSING = object()
_METHOD = object()


def uses_stdlib_constructors(*classes):
    # A patched __init__ or __new__ can act on some prefix lengths only, which
    # the samples would miss, and must not run for a sample. functools.wraps
    # does not copy __code__, so compare the file that defines __init__.
    origin = ipaddress.__spec__.origin
    return all(
        cls.__new__ is object.__new__
        and getattr(getattr(cls.__init__, '__code__', None), 'co_filename', None)
        == origin
        for cls in classes
    )


def _state(obj):
    # Methods set by __init__, such as hosts, differ by identity. Compare only
    # that both objects have them.
    state = {
        name: _METHOD if callable(value) else value
        for name, value in getattr(obj, '__dict__', {}).items()
    }
    # Slots hold state that __dict__ does not show.
    for cls in type(obj).__mro__:
        slots = vars(cls).get('__slots__', ())
        for name in (slots,) if isinstance(slots, str) else slots:
            if name not in ('__dict__', '__weakref__'):
                name = _mangle(cls, name)
                state[name] = getattr(obj, name, _MISSING)
    return state


def _mangle(cls, name):
    # Python stores a private slot, such as __tag, under its mangled name.
    owner = cls.__name__.lstrip('_')
    if owner and name.startswith('__') and not name.endswith('__'):
        return f'_{owner}{name}'
    return name


def matches(direct, constructed):
    # Compare instance state first. Some methods cache values in __dict__.
    return (
        _state(direct) == _state(constructed)
        and _state(direct.network_address) == _state(constructed.network_address)
        and direct == constructed
        and str(direct) == str(constructed)
        and hash(direct) == hash(constructed)
        and type(direct.hosts()) is type(constructed.hosts())
        and list(direct.hosts()) == list(constructed.hosts())
    )
";

/// Networks for the network check: a regular network, plus the two prefix
/// lengths where `ipaddress` overrides `hosts`.
const NETWORK_CHECK_SAMPLES: [&str; 6] = [
    "192.0.2.0/29",
    "192.0.2.0/31",
    "192.0.2.1/32",
    "2001:db8::/125",
    "2001:db8::/127",
    "2001:db8::1/128",
];

impl IpaddressCache {
    fn new(py: Python<'_>) -> PyResult<Self> {
        let mut cache = Self::load(py)?;
        if !cache.use_constructors {
            cache.use_constructors = !cache.direct_construction_matches(py)?;
        }
        Ok(cache)
    }

    fn load(py: Python<'_>) -> PyResult<Self> {
        let ipaddress = py.import("ipaddress")?;
        let ipv4_network_cls = ipaddress.getattr("IPv4Network")?;
        let ipv6_network_cls = ipaddress.getattr("IPv6Network")?;
        let ipv4_address_cls = ipaddress.getattr("IPv4Address")?;
        let ipv6_address_cls = ipaddress.getattr("IPv6Address")?;

        let mut cache = Self {
            ipv4_network_cls: ipv4_network_cls.clone().unbind(),
            ipv6_network_cls: ipv6_network_cls.clone().unbind(),
            ipv4_address_cls: ipv4_address_cls.clone().unbind(),
            ipv6_address_cls: ipv6_address_cls.clone().unbind(),
            ipv4_netmasks: Box::default(),
            ipv6_netmasks: Box::default(),
            single_host_returns_iter: false,
            use_constructors: true,
        };

        // Direct construction needs the real classes. A class replaced by
        // another callable, such as a `unittest.mock.MagicMock`, is only
        // called, as the constructors would be.
        let classes = [
            &ipv4_network_cls,
            &ipv6_network_cls,
            &ipv4_address_cls,
            &ipv6_address_cls,
        ];
        if !classes.iter().all(|class| class.is_instance_of::<PyType>()) {
            return Ok(cache);
        }

        let netmasks = netmasks(&ipv4_network_cls, 32)
            .and_then(|ipv4| Ok((ipv4, netmasks(&ipv6_network_cls, 128)?)));
        match netmasks {
            Ok((ipv4_netmasks, ipv6_netmasks)) => {
                cache.ipv4_netmasks = ipv4_netmasks;
                cache.ipv6_netmasks = ipv6_netmasks;
                cache.use_constructors = false;
            }
            Err(err) if is_mismatch_error(py, &err) => {}
            Err(err) => return Err(err),
        }
        Ok(cache)
    }

    #[inline]
    fn new_network<'py>(
        &self,
        py: Python<'py>,
        ip_net: ipnetwork::IpNetwork,
    ) -> PyResult<Bound<'py, PyAny>> {
        if self.use_constructors {
            return self.construct_network(py, ip_net);
        }

        let (net_cls, addr_cls, addr_obj, prefix_len, max_prefix, netmask, is_v6) = match ip_net {
            ipnetwork::IpNetwork::V4(v4) => {
                let prefix = v4.prefix() as usize;
                (
                    self.ipv4_network_cls.bind(py),
                    self.ipv4_address_cls.bind(py),
                    u32::from(v4.ip()).into_pyobject(py)?.into_any().unbind(),
                    prefix,
                    32,
                    self.ipv4_netmasks[prefix].bind(py),
                    false,
                )
            }
            ipnetwork::IpNetwork::V6(v6) => {
                let prefix = v6.prefix() as usize;
                (
                    self.ipv6_network_cls.bind(py),
                    self.ipv6_address_cls.bind(py),
                    u128::from(v6.ip()).into_pyobject(py)?.into_any().unbind(),
                    prefix,
                    128,
                    self.ipv6_netmasks[prefix].bind(py),
                    true,
                )
            }
        };

        let object_new = pyo3::intern!(py, "__new__");
        let addr_inst = addr_cls.call_method1(object_new, (addr_cls,))?;
        addr_inst.setattr(pyo3::intern!(py, "_ip"), addr_obj)?;
        if is_v6 {
            // Python 3.9 added the `_scope_id` slot. If a later version drops
            // it, the network check fails, and the iterator uses the
            // constructors.
            addr_inst.setattr(pyo3::intern!(py, "_scope_id"), py.None())?;
        }

        let net_inst = net_cls.call_method1(object_new, (net_cls,))?;
        net_inst.setattr(pyo3::intern!(py, "network_address"), &addr_inst)?;
        net_inst.setattr(pyo3::intern!(py, "netmask"), netmask)?;
        net_inst.setattr(pyo3::intern!(py, "_prefixlen"), prefix_len)?;
        if prefix_len == max_prefix - 1 {
            net_inst.setattr(
                pyo3::intern!(py, "hosts"),
                net_inst.getattr(pyo3::intern!(py, "__iter__"))?,
            )?;
        } else if prefix_len == max_prefix {
            if self.single_host_returns_iter {
                let tuple = PyTuple::new(py, [addr_inst])?;
                net_inst.setattr(
                    pyo3::intern!(py, "hosts"),
                    tuple.getattr(pyo3::intern!(py, "__iter__"))?,
                )?;
            } else {
                let list = PyList::new(py, [addr_inst])?;
                net_inst.setattr(
                    pyo3::intern!(py, "hosts"),
                    list.getattr(pyo3::intern!(py, "copy"))?,
                )?;
            }
        }
        Ok(net_inst)
    }

    fn direct_construction_matches(&mut self, py: Python<'_>) -> PyResult<bool> {
        let check = network_check(py)?;
        match self.check_direct_construction(py, check) {
            Ok(matches) => Ok(matches),
            Err(err) if is_mismatch_error(py, &err) => Ok(false),
            Err(err) => Err(err),
        }
    }

    /// Report whether the classes have the stdlib constructors and each sample
    /// network built directly matches the constructed one. Errors go to the
    /// caller, which decides whether they mean a mismatch.
    fn check_direct_construction(
        &mut self,
        py: Python<'_>,
        check: &NetworkCheck,
    ) -> PyResult<bool> {
        let classes = (
            &self.ipv4_network_cls,
            &self.ipv6_network_cls,
            &self.ipv4_address_cls,
            &self.ipv6_address_cls,
        );
        if !check
            .uses_stdlib_constructors
            .bind(py)
            .call1(classes)?
            .is_truthy()?
        {
            return Ok(false);
        }
        let matches = check.matches.bind(py);
        for sample in NETWORK_CHECK_SAMPLES {
            let ip_net = ipnetwork::IpNetwork::from_str(sample)
                .expect("network check samples are valid networks");
            if !self.sample_matches(py, matches, ip_net)? {
                return Ok(false);
            }
        }
        Ok(true)
    }

    /// Report whether the direct network for `ip_net` matches the constructed
    /// one.
    fn sample_matches(
        &mut self,
        py: Python<'_>,
        check: &Bound<'_, PyAny>,
        ip_net: ipnetwork::IpNetwork,
    ) -> PyResult<bool> {
        let constructed = self.construct_network(py, ip_net)?;
        if matches!(ip_net, ipnetwork::IpNetwork::V4(v4) if v4.prefix() == 32) {
            // `hosts` of a single-host network returns a list on some Python
            // versions and an iterator on others. Copy what the constructor
            // returns. This sample comes before the IPv6 /128 sample, which
            // uses the same setting.
            self.single_host_returns_iter = !constructed
                .call_method0("hosts")?
                .is_instance_of::<PyList>();
        }
        let direct = self.new_network(py, ip_net)?;
        check.call1((direct, constructed))?.is_truthy()
    }

    fn construct_network<'py>(
        &self,
        py: Python<'py>,
        ip_net: ipnetwork::IpNetwork,
    ) -> PyResult<Bound<'py, PyAny>> {
        let (class, addr_int, prefix_len) = match ip_net {
            ipnetwork::IpNetwork::V4(v4) => (
                self.ipv4_network_cls.bind(py),
                u32::from(v4.ip()) as u128,
                v4.prefix(),
            ),
            ipnetwork::IpNetwork::V6(v6) => (
                self.ipv6_network_cls.bind(py),
                u128::from(v6.ip()),
                v6.prefix(),
            ),
        };
        class.call1(((addr_int, prefix_len),))
    }
}

type MmapWithin<'a> = Within<'a, Mmap>;
type MemoryWithin<'a> = Within<'a, Vec<u8>>;

self_cell!(
    struct MmapWithinCell {
        owner: Arc<ReaderSource>,

        #[covariant]
        dependent: MmapWithin,
    }
);

self_cell!(
    struct MemoryWithinCell {
        owner: Arc<ReaderSource>,

        #[covariant]
        dependent: MemoryWithin,
    }
);

enum ReaderWithin {
    Mmap(MmapWithinCell),
    Memory(MemoryWithinCell),
}

/// Result from the within iterator, containing network and decoded data.
struct WithinResult {
    ip_net: ipnetwork::IpNetwork,
    data: PyDecodedValue,
}

#[inline]
fn process_within_lookup<S: AsRef<[u8]>>(
    lookup_result: maxminddb_crate::LookupResult<'_, S>,
) -> Result<WithinResult, MaxMindDbError> {
    let network = lookup_result.network()?;
    let ip_net = ipnetwork::IpNetwork::new(network.network(), network.prefix()).map_err(|err| {
        MaxMindDbError::InvalidDatabase {
            message: format!("Invalid network from database: {err}"),
            offset: None,
        }
    })?;
    let data: Option<PyDecodedValue> = lookup_result.decode()?;
    let data = data.ok_or_else(|| MaxMindDbError::InvalidDatabase {
        message: "No data in database record".to_string(),
        offset: None,
    })?;
    Ok(WithinResult { ip_net, data })
}

#[inline]
fn next_within<S: AsRef<[u8]>>(
    iter: &mut Within<'_, S>,
) -> Option<Result<WithinResult, MaxMindDbError>> {
    let result = iter.next()?;
    Some(result.and_then(process_within_lookup))
}

impl ReaderWithin {
    fn new(
        reader: Arc<ReaderSource>,
        network: ipnetwork::IpNetwork,
    ) -> Result<Self, maxminddb_crate::MaxMindDbError> {
        let options = WithinOptions::default();
        match reader.as_ref() {
            ReaderSource::Mmap(_) => Ok(Self::Mmap(MmapWithinCell::try_new(
                reader,
                move |reader| match reader.as_ref() {
                    ReaderSource::Mmap(inner) => inner.within(network, options),
                    ReaderSource::Memory(_) => {
                        unreachable!("reader source changed while building iterator")
                    }
                },
            )?)),
            ReaderSource::Memory(_) => Ok(Self::Memory(MemoryWithinCell::try_new(
                reader,
                move |reader| match reader.as_ref() {
                    ReaderSource::Mmap(_) => {
                        unreachable!("reader source changed while building iterator")
                    }
                    ReaderSource::Memory(inner) => inner.within(network, options),
                },
            )?)),
        }
    }

    fn next(&mut self) -> Option<Result<WithinResult, maxminddb_crate::MaxMindDbError>> {
        match self {
            ReaderWithin::Mmap(cell) => cell.with_dependent_mut(|_, iter| next_within(iter)),
            ReaderWithin::Memory(cell) => cell.with_dependent_mut(|_, iter| next_within(iter)),
        }
    }
}

/// Return the netmask address for each prefix length from 0 to `bits`. These
/// are the objects that `_make_netmask` caches, which the `ipaddress`
/// constructors also use.
fn netmasks(network_cls: &Bound<'_, PyAny>, bits: u32) -> PyResult<Box<[Py<PyAny>]>> {
    let make_netmask = network_cls.getattr(pyo3::intern!(network_cls.py(), "_make_netmask"))?;
    (0..=bits)
        .map(|prefix| Ok(make_netmask.call1((prefix,))?.get_item(0)?.unbind()))
        .collect()
}

/// Report whether an error from the netmask lookup, direct construction, or
/// the network check means the direct path does not fit this Python's
/// `ipaddress`, for example a missing attribute or a changed `__new__`
/// signature. Other errors, such as `KeyboardInterrupt` or `MemoryError`, must
/// propagate instead of switching the iterator to the constructors.
fn is_mismatch_error(py: Python<'_>, err: &PyErr) -> bool {
    err.is_instance_of::<PyTypeError>(py)
        || err.is_instance_of::<PyAttributeError>(py)
        || err.is_instance_of::<PyValueError>(py)
}

/// The compiled functions from `NETWORK_CHECK_SOURCE`.
struct NetworkCheck {
    uses_stdlib_constructors: Py<PyAny>,
    matches: Py<PyAny>,
}

static NETWORK_CHECK: PyOnceLock<NetworkCheck> = PyOnceLock::new();

/// Return the compiled functions from `NETWORK_CHECK_SOURCE`.
fn network_check(py: Python<'_>) -> PyResult<&'static NetworkCheck> {
    if let Some(check) = NETWORK_CHECK.get(py) {
        return Ok(check);
    }
    // Compile outside the lock. Compiling can run a GC finalizer, and a
    // finalizer that starts an iteration would re-enter the lock. Run the
    // source in a plain namespace so it leaves nothing in sys.modules.
    let namespace = PyDict::new(py);
    py.run(NETWORK_CHECK_SOURCE, Some(&namespace), None)?;
    let function = |name: &str| -> PyResult<Py<PyAny>> {
        Ok(namespace
            .get_item(name)?
            .ok_or_else(|| PyRuntimeError::new_err(format!("network check defines no `{name}`")))?
            .unbind())
    };
    let check = NetworkCheck {
        uses_stdlib_constructors: function("uses_stdlib_constructors")?,
        matches: function("matches")?,
    };
    Ok(NETWORK_CHECK.get_or_init(py, || check))
}
