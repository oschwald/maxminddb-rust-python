use pyo3::{
    conversion::IntoPyObjectExt,
    prelude::*,
    types::{PyBytes, PyDict, PyList, PyString},
};
use rustc_hash::FxBuildHasher;
use serde::de::{self, Deserialize, DeserializeSeed, Deserializer, MapAccess, SeqAccess, Visitor};
use std::{
    cell::RefCell,
    fmt,
    hash::{BuildHasher, Hasher},
};

thread_local! {
    static PY_MAP_KEY_CACHE: RefCell<DirectStringCache<PY_MAP_KEY_CACHE_SLOTS>> =
        RefCell::new(DirectStringCache::new());
    static PY_STRING_VALUE_CACHE: RefCell<DirectStringCache<PY_STRING_CACHE_SLOTS>> =
        RefCell::new(DirectStringCache::new());
}

const PY_MAP_KEY_CACHE_SLOTS: usize = 512;
const PY_STRING_CACHE_MAX_LEN: usize = 32;
const PY_STRING_CACHE_SLOTS: usize = 2048;

/// Wrapper that owns the Python object produced by deserializing a MaxMind record.
#[derive(Debug)]
pub(crate) struct PyDecodedValue {
    value: Py<PyAny>,
}

impl PyDecodedValue {
    #[inline]
    fn new(value: Py<PyAny>) -> Self {
        Self { value }
    }

    #[inline]
    pub(crate) fn into_py(self) -> Py<PyAny> {
        self.value
    }
}

impl<'de> Deserialize<'de> for PyDecodedValue {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        Python::attach(|py| PyValueSeed { py }.deserialize(deserializer))
    }
}

#[derive(Copy, Clone)]
struct PyValueSeed<'py> {
    py: Python<'py>,
}

impl<'py> PyValueSeed<'py> {
    #[inline]
    fn new(py: Python<'py>) -> Self {
        Self { py }
    }
}

impl<'de, 'py> DeserializeSeed<'de> for PyValueSeed<'py> {
    type Value = PyDecodedValue;

    fn deserialize<D>(self, deserializer: D) -> Result<Self::Value, D::Error>
    where
        D: Deserializer<'de>,
    {
        maxminddb::deserialize_any_with_raw_strings(deserializer, PyValueVisitor { py: self.py })
    }
}

struct PyValueVisitor<'py> {
    py: Python<'py>,
}

impl<'de, 'py> Visitor<'de> for PyValueVisitor<'py> {
    type Value = PyDecodedValue;

    fn expecting(&self, formatter: &mut fmt::Formatter) -> fmt::Result {
        formatter.write_str("any valid MaxMind DB value")
    }

    fn visit_bool<E>(self, value: bool) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        bound_to_value(value.into_py_any(self.py))
    }

    fn visit_i32<E>(self, value: i32) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        bound_to_value(value.into_py_any(self.py))
    }

    fn visit_i64<E>(self, value: i64) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        if value >= i32::MIN as i64 && value <= i32::MAX as i64 {
            bound_to_value((value as i32).into_py_any(self.py))
        } else {
            Err(E::custom(format!("integer {} out of i32 range", value)))
        }
    }

    fn visit_u16<E>(self, value: u16) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        bound_to_value(value.into_py_any(self.py))
    }

    fn visit_u32<E>(self, value: u32) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        bound_to_value(value.into_py_any(self.py))
    }

    fn visit_u64<E>(self, value: u64) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        bound_to_value(value.into_py_any(self.py))
    }

    fn visit_u128<E>(self, value: u128) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        bound_to_value(value.into_py_any(self.py))
    }

    fn visit_f32<E>(self, value: f32) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        bound_to_value(value.into_py_any(self.py))
    }

    fn visit_f64<E>(self, value: f64) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        bound_to_value(value.into_py_any(self.py))
    }

    fn visit_str<E>(self, value: &str) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        bound_to_value(value.into_py_any(self.py))
    }

    fn visit_string<E>(self, value: String) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        bound_to_value(value.into_py_any(self.py))
    }

    fn visit_newtype_struct<D>(self, deserializer: D) -> Result<Self::Value, D::Error>
    where
        D: Deserializer<'de>,
    {
        deserializer.deserialize_bytes(PyStringBytesVisitor { py: self.py })
    }

    fn visit_bytes<E>(self, value: &[u8]) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        let py_bytes = PyBytes::new(self.py, value);
        Ok(PyDecodedValue::new(py_bytes.into_any().unbind()))
    }

    fn visit_byte_buf<E>(self, value: Vec<u8>) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        let py_bytes = PyBytes::new(self.py, &value);
        Ok(PyDecodedValue::new(py_bytes.into_any().unbind()))
    }

    fn visit_seq<A>(self, mut seq: A) -> Result<Self::Value, A::Error>
    where
        A: SeqAccess<'de>,
    {
        let capacity = seq.size_hint().unwrap_or(0);
        let mut elements = Vec::with_capacity(capacity);
        while let Some(elem) = seq.next_element_seed(PyValueSeed::new(self.py))? {
            elements.push(elem.into_py());
        }
        let py_list = PyList::new(self.py, elements).map_err(pyerr_to_de_error)?;
        Ok(PyDecodedValue::new(py_list.into_any().unbind()))
    }

    fn visit_map<A>(self, mut map: A) -> Result<Self::Value, A::Error>
    where
        A: MapAccess<'de>,
    {
        let dict = PyDict::new(self.py);
        while let Some(key) = map.next_key_seed(PyMapKeySeed)? {
            let value = map.next_value_seed(PyValueSeed::new(self.py))?;
            set_cached_map_item(self.py, &dict, key, value.into_py()).map_err(pyerr_to_de_error)?;
        }
        Ok(PyDecodedValue::new(dict.into_any().unbind()))
    }
}

struct PyStringBytesVisitor<'py> {
    py: Python<'py>,
}

impl<'de, 'py> Visitor<'de> for PyStringBytesVisitor<'py> {
    type Value = PyDecodedValue;

    fn expecting(&self, formatter: &mut fmt::Formatter) -> fmt::Result {
        formatter.write_str("borrowed MaxMind DB string bytes")
    }

    fn visit_borrowed_bytes<E>(self, value: &'de [u8]) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        bound_to_value(cached_string_value(self.py, value))
    }
}

struct PyMapKeySeed;

impl<'de> DeserializeSeed<'de> for PyMapKeySeed {
    type Value = &'de [u8];

    fn deserialize<D>(self, deserializer: D) -> Result<Self::Value, D::Error>
    where
        D: Deserializer<'de>,
    {
        deserializer.deserialize_identifier(PyMapKeyVisitor)
    }
}

struct PyMapKeyVisitor;

impl<'de> Visitor<'de> for PyMapKeyVisitor {
    type Value = &'de [u8];

    fn expecting(&self, formatter: &mut fmt::Formatter) -> fmt::Result {
        formatter.write_str("borrowed MaxMind DB map-key bytes")
    }

    fn visit_borrowed_bytes<E>(self, value: &'de [u8]) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        Ok(value)
    }
}

fn pyerr_to_de_error<E: de::Error>(err: PyErr) -> E {
    E::custom(err.to_string())
}

fn bound_to_value<E: de::Error>(result: PyResult<Py<PyAny>>) -> Result<PyDecodedValue, E> {
    result.map(PyDecodedValue::new).map_err(pyerr_to_de_error)
}

fn set_cached_map_item(
    py: Python,
    dict: &Bound<'_, PyDict>,
    key: &[u8],
    value: Py<PyAny>,
) -> PyResult<()> {
    if key.len() > PY_STRING_CACHE_MAX_LEN {
        return dict.set_item(PyString::from_bytes(py, key)?, value);
    }
    PY_MAP_KEY_CACHE.with(|cache| {
        let slot = match cache.borrow().lookup(key) {
            // Borrow the cached key instead of taking a new reference. This
            // saves an incref and decref per map key. Holding the shared
            // borrow across `set_item` is safe: it allocates no
            // GC-tracked object, so it cannot start a GC pass. A duplicate
            // key frees only an earlier decoded value, which has no
            // finalizer.
            Ok(cached) => return dict.set_item(cached.bind_borrowed(py), value),
            Err(slot) => slot,
        };
        // Create the string with no borrow held. Invalid UTF-8 raises an
        // error, creating it can trigger a GC pass, and a finalizer that
        // decodes a record would find the cache borrowed and panic.
        let py_key = PyString::from_bytes(py, key)?;
        dict.set_item(&py_key, value)?;
        cache.borrow_mut().insert(slot, key, py_key.unbind());
        Ok(())
    })
}

fn cached_string_value(py: Python, value: &[u8]) -> PyResult<Py<PyAny>> {
    if value.len() > PY_STRING_CACHE_MAX_LEN {
        return Ok(PyString::from_bytes(py, value)?.into_any().unbind());
    }
    PY_STRING_VALUE_CACHE.with(|cache| {
        let slot = match cache.borrow().lookup(value) {
            Ok(cached) => return Ok(cached.clone_ref(py).into_any()),
            Err(slot) => slot,
        };
        // See `set_cached_map_item` for why no borrow is held here.
        let py_str = PyString::from_bytes(py, value)?.unbind();
        let result = py_str.clone_ref(py).into_any();
        cache.borrow_mut().insert(slot, value, py_str);
        Ok(result)
    })
}

struct StringCacheEntry {
    len: u8,
    bytes: [u8; PY_STRING_CACHE_MAX_LEN],
    value: Option<Py<PyString>>,
}

struct DirectStringCache<const SLOTS: usize> {
    entries: Box<[StringCacheEntry]>,
}

impl<const SLOTS: usize> DirectStringCache<SLOTS> {
    fn new() -> Self {
        // `slot_index` masks the hash, so the slot count must be a power of two.
        const { assert!(SLOTS.is_power_of_two()) };
        let mut entries = Vec::with_capacity(SLOTS);
        for _ in 0..SLOTS {
            entries.push(StringCacheEntry {
                len: 0,
                bytes: [0; PY_STRING_CACHE_MAX_LEN],
                value: None,
            });
        }
        Self {
            entries: entries.into_boxed_slice(),
        }
    }

    /// Return the cached string for `bytes`, or the slot to insert it into
    /// on a miss. `bytes` must fit in a cache entry.
    #[inline]
    fn lookup(&self, bytes: &[u8]) -> Result<&Py<PyString>, usize> {
        let slot = Self::slot_index(bytes);
        let entry = &self.entries[slot];
        let hit = usize::from(entry.len) == bytes.len() && &entry.bytes[..bytes.len()] == bytes;
        match entry.value {
            Some(ref cached) if hit => Ok(cached),
            _ => Err(slot),
        }
    }

    /// Replace the entry in `slot` with `value` for `bytes`.
    #[inline]
    fn insert(&mut self, slot: usize, bytes: &[u8], value: Py<PyString>) {
        let entry = &mut self.entries[slot];
        entry.len = bytes.len() as u8;
        entry.bytes[..bytes.len()].copy_from_slice(bytes);
        entry.value = Some(value);
    }

    #[inline(always)]
    fn slot_index(value: &[u8]) -> usize {
        let mut hasher = FxBuildHasher.build_hasher();
        hasher.write(value);
        (hasher.finish() as usize) & (SLOTS - 1)
    }
}
