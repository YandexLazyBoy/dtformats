# -*- coding: utf-8 -*-
"""WMI Common Information Model (CIM) repository files."""

import datetime
import glob
import logging
import os

from dtfabric import data_maps as dtfabric_data_maps
from dtfabric import errors as dtfabric_errors
from dtfabric import fabric as dtfabric_fabric

from dtformats import data_format
from dtformats import errors


def FromFiletime(filetime):
  """Converts a FILETIME timestamp into a Python datetime object.

  The FILETIME is mainly used in Windows file formats and NTFS.

  The FILETIME is a 64-bit value containing 100th nano seconds since
  1601-01-01 00:00:00

  Technically FILETIME consists of 2 x 32-bit parts and is presumed
  to be unsigned.

  Args:
    filetime (int): 64-bit FILETIME timestamp.

  Returns:
    datetime.datetime: date and time or None.
  """
  if filetime < 0:
    return None
  timestamp, _ = divmod(filetime, 10)

  return datetime.datetime(1601, 1, 1) + datetime.timedelta(
      microseconds=timestamp)


class PropertyDescriptor(object):
  """Property descriptor.

  Attributes:
    definition_offset (int): offset of the property definition.
    name_offset (int): offset of the property name.
  """

  def __init__(self, name_offset, definition_offset):
    """Initializes a property descriptor.

    Args:
      name_offset (int): offset of the property name.
      definition_offset (int): offset of the property definition.
    """
    super(PropertyDescriptor, self).__init__()
    self.definition_offset = definition_offset
    self.name_offset = name_offset


class IndexBinaryTreePage(data_format.BinaryDataFormat):
  """Index binary-tree page.

  Attributes:
    keys (list[str]): index binary-tree keys.
    page_type (int): page type.
    root_page_number (int): root page number.
    sub_pages (list[int]): sub page numbers.
  """

  _DATA_TYPE_FABRIC_DEFINITION = b'\n'.join([
      b'name: byte',
      b'type: integer',
      b'attributes:',
      b'  format: unsigned',
      b'  size: 1',
      b'  units: bytes',
      b'---',
      b'name: uint16',
      b'type: integer',
      b'attributes:',
      b'  format: unsigned',
      b'  size: 2',
      b'  units: bytes',
      b'---',
      b'name: uint32',
      b'type: integer',
      b'attributes:',
      b'  format: unsigned',
      b'  size: 4',
      b'  units: bytes',
      b'---',
      b'name: uint16le',
      b'type: integer',
      b'attributes:',
      b'  byte_order: little-endian',
      b'  format: unsigned',
      b'  size: 2',
      b'  units: bytes',
      b'---',
      b'name: uint32le',
      b'type: integer',
      b'attributes:',
      b'  byte_order: little-endian',
      b'  format: unsigned',
      b'  size: 4',
      b'  units: bytes',
      b'---',
      b'name: cim_page_header',
      b'type: structure',
      b'attributes:',
      b'  byte_order: little-endian',
      b'members:',
      b'- name: page_type',
      b'  data_type: uint32',
      b'- name: mapped_page_number',
      b'  data_type: uint32',
      b'- name: unknown1',
      b'  data_type: uint32',
      b'- name: root_page_number',
      b'  data_type: uint32',
      b'- name: number_of_keys',
      b'  data_type: uint32',
      b'---',
      b'name: cim_page_offsets',
      b'type: sequence',
      b'element_data_type: uint16le',
      b'number_of_elements: cim_page_header.number_of_keys',
      b'---',
      b'name: cim_page_subpages',
      b'type: sequence',
      b'element_data_type: uint32le',
      b'number_of_elements: cim_page_header.number_of_keys + 1',
      b'---',
      b'name: cim_page_key',
      b'type: structure',
      b'attributes:',
      b'  byte_order: little-endian',
      b'members:',
      b'- name: number_of_segments',
      b'  data_type: uint16',
      b'- name: segments',
      b'  type: sequence',
      b'  element_data_type: uint16',
      b'  number_of_elements: cim_page_key.number_of_segments',
      b'---',
      b'name: cim_offsets',
      b'type: sequence',
      b'element_data_type: uint16le',
      b'number_of_elements: number_of_offsets',
      b'---',
      b'name: string',
      b'type: string',
      b'encoding: ascii',
      b'element_data_type: byte',
      b'elements_terminator: "\\x00"',
  ])

  _DATA_TYPE_FABRIC = dtfabric_fabric.DataTypeFabric(
      yaml_definition=_DATA_TYPE_FABRIC_DEFINITION)

  _UINT16LE = _DATA_TYPE_FABRIC.CreateDataTypeMap(u'uint16le')

  _UINT16LE_SIZE = _UINT16LE.GetByteSize()

  _PAGE_HEADER = _DATA_TYPE_FABRIC.CreateDataTypeMap(u'cim_page_header')

  _PAGE_HEADER_SIZE = _PAGE_HEADER.GetByteSize()

  _PAGE_KEY_OFFSETS = _DATA_TYPE_FABRIC.CreateDataTypeMap(u'cim_page_offsets')

  _PAGE_SUBPAGES = _DATA_TYPE_FABRIC.CreateDataTypeMap(u'cim_page_subpages')

  _PAGE_KEY = _DATA_TYPE_FABRIC.CreateDataTypeMap(u'cim_page_key')

  _OFFSETS = _DATA_TYPE_FABRIC.CreateDataTypeMap(u'cim_offsets')

  _STRING = _DATA_TYPE_FABRIC.CreateDataTypeMap(u'string')

  _PAGE_TYPES = {
      0xaccc: u'Is active',
      0xaddd: u'Is administrative',
      0xbadd: u'Is deleted',
  }

  _KEY_SEGMENT_SEPARATOR = u'\\'

  PAGE_SIZE = 8192

  def __init__(self, debug=False, output_writer=None):
    """Initializes an index binary-tree page.

    Args:
      debug (Optional[bool]): True if debug information should be written.
      output_writer (Optional[OutputWriter]): output writer.
    """
    super(IndexBinaryTreePage, self).__init__(
        debug=debug, output_writer=output_writer)
    self._key_offsets = None
    self._number_of_keys = None
    self._page_key_segments = []
    self._page_values = []
    self._page_value_offsets = None

    self.keys = []
    self.page_type = None
    self.root_page_number = None
    self.sub_pages = []

  def _DebugPrintHeader(self, page_header):
    """Prints page header debug information.

    Args:
      page_header (cim_page_header): page header.
    """
    page_type_string = self._PAGE_TYPES.get(page_header.page_type, u'Unknown')
    value_string = u'0x{0:04x} ({1:s})'.format(
        page_header.page_type, page_type_string)
    self._DebugPrintValue(u'Page type', value_string)

    self._DebugPrintValueDecimal(
        u'Mapped page number', page_header.mapped_page_number)

    value_string = u'0x{0:08x}'.format(page_header.unknown1)
    self._DebugPrintValue(u'Unknown1', value_string)

    self._DebugPrintValueDecimal(
        u'Root page number', page_header.root_page_number)

    self._DebugPrintValueDecimal(u'Number of keys', page_header.number_of_keys)

    self._DebugPrintText(u'\n')

  def _DebugPrintKeyOffsets(self, key_offsets):
    """Prints key offsets debug information.

    Args:
      key_offsets (list[int]): key offsets.
    """
    for index, key_offset in enumerate(key_offsets):
      description = u'Page key: {0:d} offset'.format(index)
      value_string = u'0x{0:04x}'.format(key_offset)
      self._DebugPrintValue(description, value_string)

    self._DebugPrintText(u'\n')

  def _DebugPrintPageNumber(
      self, description, page_number, unavailable_page_numbers=None):
    """Prints a page number debug information.

    Args:
      description (str): description.
      page_number (int): page number.
      unavailable_page_numbers (Optional[set[int]]): unavailable page numbers.
    """
    if not unavailable_page_numbers:
      unavailable_page_numbers = set()

    if page_number in unavailable_page_numbers:
      value_string = u'0x{0:08x} (unavailable)'.format(page_number)
    else:
      value_string = u'{0:d}'.format(page_number)

    self._DebugPrintValue(description, value_string)

  def _ReadHeader(self, file_object):
    """Reads a page header.

    Args:
      file_object (file): a file-like object.

    Returns:
      cim_page_header: page header.

    Raises:
      ParseError: if the page header cannot be read.
    """
    file_offset = file_object.tell()

    page_header = self._ReadStructure(
        file_object, file_offset, self._PAGE_HEADER_SIZE, self._PAGE_HEADER,
        u'page header')

    if self._debug:
      self._DebugPrintHeader(page_header)

    self.page_type = page_header.page_type
    self.root_page_number = page_header.root_page_number
    self._number_of_keys = page_header.number_of_keys

    return page_header

  def _ReadKeyOffsets(self, page_header, file_object):
    """Reads page key offsets.

    Args:
      page_header (cim_page_header): page header.
      file_object (file): a file-like object.

    Raises:
      ParseError: if the page key offsets cannot be read.
    """
    if page_header.number_of_keys == 0:
      return

    file_offset = file_object.tell()
    if self._debug:
      self._DebugPrintText(
          u'Reading page key offsets at offset: 0x{0:08x}\n'.format(
              file_offset))

    offsets_data_size = page_header.number_of_keys * 2
    offsets_data = file_object.read(offsets_data_size)

    if self._debug:
      self._DebugPrintData(u'Page key offsets data', offsets_data)

    context = dtfabric_data_maps.DataTypeMapContext(values={
        u'cim_page_header': page_header})

    try:
      self._key_offsets = self._PAGE_KEY_OFFSETS.MapByteStream(
          offsets_data, context=context)
    except dtfabric_errors.MappingError as exception:
      raise errors.ParseError((
          u'Unable to parse page key offsets at offset: 0x{0:08x} '
          u'with error: {1:s}').format(file_offset, exception))

    if self._debug:
      self._DebugPrintKeyOffsets(self._key_offsets)

  def _ReadKeyData(self, file_object):
    """Reads page key data.

    Args:
      file_object (file): a file-like object.

    Raises:
      ParseError: if the page key data cannot be read.
    """
    file_offset = file_object.tell()
    if self._debug:
      self._DebugPrintText(
          u'Reading page key data at offset: 0x{0:08x}\n'.format(file_offset))

    size_data = file_object.read(self._UINT16LE_SIZE)

    try:
      data_size = self._UINT16LE.MapByteStream(size_data)
    except dtfabric_errors.MappingError as exception:
      raise errors.ParseError((
          u'Unable to parse page key data size at offset: 0x{0:08x} '
          u'with error: {1:s}').format(file_offset, exception))

    if self._debug:
      value_string = u'{0:d} ({1:d} bytes)'.format(data_size, data_size * 2)
      self._DebugPrintValue(u'Page key data size', value_string)

    if data_size == 0:
      if self._debug:
        self._DebugPrintData(u'Page key data', size_data)
      return

    key_data = file_object.read(data_size * 2)

    if self._debug:
      self._DebugPrintData(u'Page key data', b''.join([size_data, key_data]))

    for index, key_offset in enumerate(self._key_offsets):
      page_key_offset = key_offset * 2

      if self._debug:
        description = u'Page key: {0:d} offset'.format(index)
        value_string = u'{0:d} (0x{0:08x})'.format(page_key_offset)
        self._DebugPrintValue(description, value_string)

      try:
        page_key = self._PAGE_KEY.MapByteStream(key_data[page_key_offset:])
      except dtfabric_errors.MappingError as exception:
        raise errors.ParseError(
            u'Unable to parse page key: {0:d} with error: {1:s}'.format(
                index, exception))

      page_key_size = page_key_offset + 2 + (page_key.number_of_segments * 2)

      if self._debug:
        description = u'Page key: {0:d} data:'.format(index)
        self._DebugPrintData(
            description, key_data[page_key_offset:page_key_size])

      self._page_key_segments.append(page_key.segments)

      if self._debug:
        description = u'Page key: {0:d} number of segments'.format(index)
        self._DebugPrintValueDecimal(description, page_key.number_of_segments)

        description = u'Page key: {0:d} segments'.format(index)
        value_string = u', '.join([
            u'{0:d}'.format(segment_index)
            for segment_index in page_key.segments])
        self._DebugPrintValue(description, value_string)

        self._DebugPrintText(u'\n')

  def _ReadOffsetsTable(self, file_object, file_offset, description):
    """Reads an offsets table.

    Args:
      file_object (file): a file-like object.
      file_offset (int): offset of the data relative from the start of
          the file-like object.
      description (str): description of the offsets table.

    Returns:
      tuple[int, ...]: offsets number array.

    Raises:
      ParseError: if the offsets table cannot be read.
    """
    if self._debug:
      self._DebugPrintText(u'Reading {0:s} at offset: 0x{1:08x}\n'.format(
          description, file_offset))

    try:
      number_of_offsets_data = file_object.read(self._UINT16LE_SIZE)
    except IOError as exception:
      raise errors.ParseError((
          u'Unable to read number of offsets data at offset: 0x{0:08x} '
          u'with error: {1:s}').format(file_offset, exception))

    if len(number_of_offsets_data) != self._UINT16LE_SIZE:
      raise errors.ParseError((
          u'Unable to read number of offsets data at offset: 0x{0:08x} '
          u'with error: missing data').format(file_offset))

    try:
      number_of_offsets = self._UINT16LE.MapByteStream(number_of_offsets_data)
    except dtfabric_errors.MappingError as exception:
      raise errors.ParseError((
          u'Unable to parse number of offsets at offset: 0x{0:08x} with error '
          u'error: {1:s}').format(file_offset, exception))

    if number_of_offsets == 0:
      offsets_data = b''
    else:
      offsets_data_size = number_of_offsets * self._UINT16LE_SIZE

      try:
        offsets_data = file_object.read(offsets_data_size)
      except IOError as exception:
        raise errors.ParseError((
            u'Unable to read offsets data at offset: 0x{0:08x} with error: '
            u'{1:s}').format(file_offset, exception))

      if len(offsets_data) != offsets_data_size:
        raise errors.ParseError((
            u'Unable to read offsets data at offset: 0x{0:08x} with error: '
            u'missing data').format(file_offset))

    if self._debug:
      data_description = u'{0:s} data'.format(description.title())
      self._DebugPrintData(data_description, b''.join([
          number_of_offsets_data, offsets_data]))

      self._DebugPrintValueDecimal(u'Number of offsets', number_of_offsets)

    if not offsets_data:
      offsets = tuple()
    else:
      context = dtfabric_data_maps.DataTypeMapContext(values={
          u'number_of_offsets': number_of_offsets})

      try:
        offsets = self._OFFSETS.MapByteStream(offsets_data, context=context)

      except dtfabric_errors.MappingError as exception:
        raise errors.ParseError((
            u'Unable to parse offsets data at offset: 0x{0:08x} with error: '
            u'{1:s}').format(file_offset, exception))

    return offsets

  def _ReadValueOffsets(self, file_object):
    """Reads page value offsets.

    Args:
      file_object (file): a file-like object.

    Raises:
      ParseError: if the page value offsets cannot be read.
    """
    file_offset = file_object.tell()
    offset_array = self._ReadOffsetsTable(
        file_object, file_offset, u'page value offsets')

    if self._debug:
      for index, offset in enumerate(offset_array):
        description = u'Page value: {0:d} offset'.format(index)
        value_string = u'0x{0:04x}'.format(offset)
        self._DebugPrintValue(description, value_string)

      self._DebugPrintText(u'\n')

    self._page_value_offsets = offset_array

  def _ReadValueData(self, file_object):
    """Reads page value data.

    Args:
      file_object (file): a file-like object.

    Raises:
      ParseError: if the page value data cannot be read.
    """
    file_offset = file_object.tell()
    if self._debug:
      self._DebugPrintText(
          u'Reading page value data at offset: 0x{0:08x}\n'.format(file_offset))

    size_data = file_object.read(self._UINT16LE_SIZE)

    try:
      data_size = self._UINT16LE.MapByteStream(size_data)
    except dtfabric_errors.MappingError as exception:
      raise errors.ParseError((
          u'Unable to parse page value data size at offset: 0x{0:08x} '
          u'with error: {1:s}').format(file_offset, exception))

    if self._debug:
      value_string = u'{0:d} bytes'.format(data_size)
      self._DebugPrintValue(u'Page value data size', value_string)

    if data_size == 0:
      self._DebugPrintData(u'Page value data', size_data)
      return

    value_data = file_object.read(data_size)

    if self._debug:
      self._DebugPrintData(u'Page value data', b''.join([
          size_data, value_data]))

    for index, page_value_offset in enumerate(self._page_value_offsets):
      # TODO: determine size

      try:
        value_string = self._STRING.MapByteStream(
            value_data[page_value_offset:])
      except dtfabric_errors.MappingError as exception:
        raise errors.ParseError((
            u'Unable to parse page value: {0:d} string with error: '
            u'{1!s}').format(index, exception))

      if self._debug:
        description = u'Page value: {0:d} data'.format(index)
        self._DebugPrintValue(description, value_string)

      self._page_values.append(value_string)

    if self._debug and self._page_value_offsets:
      self._DebugPrintText(u'\n')

  def _ReadSubPages(self, page_header, file_object):
    """Reads sub pages data.

    Args:
      page_header (cim_page_header): page header.
      file_object (file): a file-like object.

    Raises:
      ParseError: if the sub pages cannot be read.
    """
    file_offset = file_object.tell()
    if self._debug:
      self._DebugPrintText(
          u'Reading sub pages at offset: 0x{0:08x}\n'.format(file_offset))

    number_of_entries = self._number_of_keys + 1
    entries_data_size = number_of_entries * 4

    entries_data = file_object.read(entries_data_size)

    if self._debug:
      self._DebugPrintData(u'Sub pages array data', entries_data)

    context = dtfabric_data_maps.DataTypeMapContext(values={
        u'cim_page_header': page_header})

    try:
      sub_pages_array = self._PAGE_SUBPAGES.MapByteStream(
          entries_data, context=context)

    except dtfabric_errors.MappingError as exception:
      raise errors.ParseError((
          u'Unable to parse sub pages at offset: 0x{0:08x} '
          u'with error: {1:s}').format(file_offset, exception))

    for index, page_number in enumerate(sub_pages_array):
      if page_number not in (0, 0xffffffff):
        self.sub_pages.append(page_number)

      if self._debug:
        description = u'Sub page: {0:d} mapped page number'.format(index)
        self._DebugPrintPageNumber(
            description, page_number,
            unavailable_page_numbers=set([0, 0xffffffff]))

    if self._debug:
      self._DebugPrintText(u'\n')

  def ReadPage(self, file_object, file_offset):
    """Reads a page.

    Args:
      file_object (file): a file-like object.
      file_offset (int): offset of the page relative from the start of the file.

    Raises:
      ParseError: if the page cannot be read.
    """
    file_object.seek(file_offset, os.SEEK_SET)

    if self._debug:
      self._DebugPrintText(
          u'Reading index binary-tree page at offset: 0x{0:08x}\n'.format(
              file_offset))

    page_header = self._ReadHeader(file_object)

    if page_header.number_of_keys > 0:
      array_data_size = page_header.number_of_keys * 4
      array_data = file_object.read(array_data_size)

      if self._debug:
        self._DebugPrintData(u'Unknown array data', array_data)

    self._ReadSubPages(page_header, file_object)
    self._ReadKeyOffsets(page_header, file_object)
    self._ReadKeyData(file_object)
    self._ReadValueOffsets(file_object)
    self._ReadValueData(file_object)

    trailing_data_size = (
        (file_offset + self.PAGE_SIZE) - file_object.tell())
    trailing_data = file_object.read(trailing_data_size)

    if self._debug:
      self._DebugPrintData(u'Trailing data', trailing_data)

    self.keys = []
    for page_key_segments in self._page_key_segments:
      key_segments = []
      for segment_index in page_key_segments:
        key_segments.append(self._page_values[segment_index])

      key_path = u'{0:s}{1:s}'.format(
          self._KEY_SEGMENT_SEPARATOR,
          self._KEY_SEGMENT_SEPARATOR.join(key_segments))

      self.keys.append(key_path)


class ObjectRecord(data_format.BinaryDataFormat):
  """Object record.

  Attributes:
    data (bytes): object record data.
    data_type (str): object record data type.
  """

  _DATA_TYPE_FABRIC_DEFINITION = b'\n'.join([
      b'name: byte',
      b'type: integer',
      b'attributes:',
      b'  format: unsigned',
      b'  size: 1',
      b'  units: bytes',
      b'---',
      b'name: uint16',
      b'type: integer',
      b'attributes:',
      b'  format: unsigned',
      b'  size: 2',
      b'  units: bytes',
      b'---',
      b'name: uint32',
      b'type: integer',
      b'attributes:',
      b'  format: unsigned',
      b'  size: 4',
      b'  units: bytes',
      b'---',
      b'name: uint64',
      b'type: integer',
      b'attributes:',
      b'  format: unsigned',
      b'  size: 8',
      b'  units: bytes',
      b'---',
      b'name: wchar16',
      b'type: character',
      b'attributes:',
      b'  size: 2',
      b'  units: bytes',
      b'---',
      b'name: cim_property_types',
      b'type: enumeration',
      b'values:',
      b'- name: CIM-TYPE-SINT16',
      b'  number: 0x00000002',
      b'- name: CIM-TYPE-SINT32',
      b'  number: 0x00000003',
      b'- name: CIM-TYPE-REAL32',
      b'  number: 0x00000004',
      b'- name: CIM-TYPE-REAL64',
      b'  number: 0x00000005',
      b'- name: CIM-TYPE-STRING',
      b'  number: 0x00000008',
      b'- name: CIM-TYPE-BOOLEAN',
      b'  number: 0x0000000b',
      b'- name: CIM-TYPE-OBJECT',
      b'  number: 0x0000000d',
      b'- name: CIM-TYPE-SINT8',
      b'  number: 0x00000010',
      b'- name: CIM-TYPE-UINT8',
      b'  number: 0x00000011',
      b'- name: CIM-TYPE-UINT16',
      b'  number: 0x00000012',
      b'- name: CIM-TYPE-UINT32',
      b'  number: 0x00000013',
      b'- name: CIM-TYPE-SINT64',
      b'  number: 0x00000014',
      b'- name: CIM-TYPE-UINT64',
      b'  number: 0x00000015',
      b'- name: CIM-TYPE-DATETIME',
      b'  number: 0x00000065',
      b'- name: CIM-TYPE-REFERENCE',
      b'  number: 0x00000066',
      b'- name: CIM-TYPE-CHAR16',
      b'  number: 0x00000067',
      b'- name: CIM-ARRAY-SINT16',
      b'  number: 0x00002002',
      b'- name: CIM-ARRAY-SINT32',
      b'  number: 0x00002003',
      b'- name: CIM-ARRAY-REAL32',
      b'  number: 0x00002004',
      b'- name: CIM-ARRAY-REAL64',
      b'  number: 0x00002005',
      b'- name: CIM-ARRAY-STRING',
      b'  number: 0x00002008',
      b'- name: CIM-ARRAY-BOOLEAN',
      b'  number: 0x0000200b',
      b'- name: CIM-ARRAY-OBJECT',
      b'  number: 0x0000200d',
      b'- name: CIM-ARRAY-SINT8',
      b'  number: 0x00002010',
      b'- name: CIM-ARRAY-UINT8',
      b'  number: 0x00002011',
      b'- name: CIM-ARRAY-UINT16',
      b'  number: 0x00002012',
      b'- name: CIM-ARRAY-UINT32',
      b'  number: 0x00002013',
      b'- name: CIM-ARRAY-SINT64',
      b'  number: 0x00002014',
      b'- name: CIM-ARRAY-UINT64',
      b'  number: 0x00002015',
      b'- name: CIM-ARRAY-DATETIME',
      b'  number: 0x00002065',
      b'- name: CIM-ARRAY-REFERENCE',
      b'  number: 0x00002066',
      b'- name: CIM-ARRAY-CHAR16',
      b'  number: 0x00002067',
      b'---',
      b'name: property_descriptor',
      b'type: structure',
      b'attributes:',
      b'  byte_order: little-endian',
      b'members:',
      b'- name: name_offset',
      b'  data_type: uint32',
      b'- name: data_offset',
      b'  data_type: uint32',
      b'---',
      b'name: block',
      b'type: structure',
      b'attributes:',
      b'  byte_order: little-endian',
      b'members:',
      b'- name: size',
      b'  data_type: uint32',
      b'- name: data',
      b'  type: stream',
      b'  element_data_type: byte',
      b'  elements_data_size: block.size - 4 if block.size else 0',
      b'---',
      b'name: class_definition_header',
      b'type: structure',
      b'attributes:',
      b'  byte_order: little-endian',
      b'members:',
      b'- name: unknown1',
      b'  data_type: byte',
      b'- name: class_name_offset',
      b'  data_type: uint32',
      b'- name: default_value_size',
      b'  data_type: uint32',
      b'- name: super_class_name_block_size',
      b'  data_type: uint32',
      b'- name: super_class_name_block_data',
      b'  type: stream',
      b'  element_data_type: byte',
      (b'  elements_data_size: '
       b'class_definition_header.super_class_name_block_size - 4'),
      b'- name: qualifiers_block_size',
      b'  data_type: uint32',
      b'- name: qualifiers_block_data',
      b'  type: stream',
      b'  element_data_type: byte',
      (b'  elements_data_size: '
       b'class_definition_header.qualifiers_block_size - 4'),
      b'- name: number_of_property_descriptors',
      b'  data_type: uint32',
      b'- name: property_descriptors',
      b'  type: sequence',
      b'  element_data_type: property_descriptor',
      (b'  number_of_elements: '
       b'class_definition_header.number_of_property_descriptors'),
      b'- name: default_value_data',
      b'  type: stream',
      b'  element_data_type: byte',
      b'  elements_data_size: class_definition_header.default_value_size',
      b'- name: properties_block_size',
      b'  data_type: uint32',
      b'- name: properties_block_data',
      b'  type: stream',
      b'  element_data_type: byte',
      (b'  elements_data_size: '
       b'class_definition_header.properties_block_size - & 0x7ffffff'),
      b'---',
      b'name: class_definition_object_record',
      b'type: structure',
      b'attributes:',
      b'  byte_order: little-endian',
      b'members:',
      b'- name: super_class_name_size',
      b'  data_type: uint32',
      b'- name: super_class_name',
      b'  type: string',
      b'  encoding: utf-16-le',
      b'  element_data_type: wchar16',
      b'  number_of_elements: super_class_name_size',
      b'- name: date_time',
      b'  data_type: uint64',
      b'- name: data_size',
      b'  data_type: uint32',
      b'- name: data',
      b'  type: stream',
      b'  element_data_type: byte',
      b'  elements_data_size: class_definition_object_record.data_size - 4',
      b'---',
      b'name: class_definition_methods',
      b'type: structure',
      b'attributes:',
      b'  byte_order: little-endian',
      b'members:',
      b'- name: methods_block_size',
      b'  data_type: uint32',
      b'- name: data',
      b'  type: stream',
      b'  element_data_type: byte',
      b'  elements_data_size: class_definition_methods.methods_block_size - 4',
      b'---',
      b'name: super_class_name_block',
      b'type: structure',
      b'attributes:',
      b'  byte_order: little-endian',
      b'members:',
      b'- name: super_class_name_flags',
      b'  data_type: byte',
      b'- name: super_class_name',
      b'  type: string',
      b'  encoding: ascii',
      b'  element_data_type: byte',
      b'  elements_terminator: "\\x00"',
      b'- name: super_class_name_size',
      b'  data_type: uint32',
      b'---',
      b'name: property_name',
      b'type: structure',
      b'attributes:',
      b'  byte_order: little-endian',
      b'members:',
      b'- name: string_flags',
      b'  data_type: byte',
      b'- name: string',
      b'  type: string',
      b'  encoding: ascii',
      b'  element_data_type: byte',
      b'  elements_terminator: "\\x00"',
      b'---',
      b'name: property_definition',
      b'type: structure',
      b'attributes:',
      b'  byte_order: little-endian',
      b'members:',
      b'- name: type',
      b'  data_type: uint32',
      b'- name: index',
      b'  data_type: uint16',
      b'- name: offset',
      b'  data_type: uint32',
      b'- name: level',
      b'  data_type: uint32',
      b'- name: qualifiers_block_size',
      b'  data_type: uint32',
      b'- name: qualifiers_block_data',
      b'  type: stream',
      b'  element_data_type: byte',
      b'  elements_data_size: property_definition.qualifiers_block_size - 4',
      b'---',
      b'name: interface_object_record',
      b'type: structure',
      b'attributes:',
      b'  byte_order: little-endian',
      b'members:',
      b'- name: string_digest_hash',
      b'  type: stream',
      b'  element_data_type: byte',
      b'  elements_data_size: 64',
      b'- name: date_time1',
      b'  data_type: uint64',
      b'- name: date_time2',
      b'  data_type: uint64',
      b'- name: data_size',
      b'  data_type: uint32',
      b'- name: data',
      b'  type: stream',
      b'  element_data_type: byte',
      b'  elements_data_size: interface_object_record.data_size - 4',
      b'---',
      b'name: registration_object_record',
      b'type: structure',
      b'attributes:',
      b'  byte_order: little-endian',
      b'members:',
      b'- name: name_space_string_size',
      b'  data_type: uint32',
      b'- name: name_space_string',
      b'  type: string',
      b'  encoding: utf-16-le',
      b'  element_data_type: wchar16',
      (b'  number_of_elements: '
       b'registration_object_record.name_space_string_size'),
      b'- name: class_name_string_size',
      b'  data_type: uint32',
      b'- name: class_name_string',
      b'  type: string',
      b'  encoding: utf-16-le',
      b'  element_data_type: wchar16',
      (b'  number_of_elements: '
       b'registration_object_record.class_name_string_size'),
      b'- name: attribute_name_string_size',
      b'  data_type: uint32',
      b'- name: attribute_name_string',
      b'  type: string',
      b'  encoding: utf-16-le',
      b'  element_data_type: wchar16',
      (b'  number_of_elements: '
       b'registration_object_record.attribute_name_string_size'),
      b'- name: attribute_value_string_size',
      b'  data_type: uint32',
      b'- name: attribute_value_string',
      b'  type: string',
      b'  encoding: utf-16-le',
      b'  element_data_type: wchar16',
      (b'  number_of_elements: '
       b'registration_object_record.attribute_value_string_size'),
      b'- name: unknown1',
      b'  type: stream',
      b'  element_data_type: byte',
      b'  elements_data_size: 8',
  ])

  # TODO: replace streams by block type
  # TODO: add more values.

  _DATA_TYPE_FABRIC = dtfabric_fabric.DataTypeFabric(
      yaml_definition=_DATA_TYPE_FABRIC_DEFINITION)

  _CLASS_DEFINITION_HEADER = _DATA_TYPE_FABRIC.CreateDataTypeMap(
      u'class_definition_header')

  _CLASS_DEFINITION_OBJECT_RECORD = _DATA_TYPE_FABRIC.CreateDataTypeMap(
      u'class_definition_object_record')

  _CLASS_DEFINITION_METHODS = _DATA_TYPE_FABRIC.CreateDataTypeMap(
      u'class_definition_methods')

  _SUPER_CLASS_NAME_BLOCK = _DATA_TYPE_FABRIC.CreateDataTypeMap(
      u'super_class_name_block')

  _PROPERTY_NAME = _DATA_TYPE_FABRIC.CreateDataTypeMap(
      u'property_name')

  _PROPERTY_DEFINITION = _DATA_TYPE_FABRIC.CreateDataTypeMap(
      u'property_definition')

  _INTERFACE_OBJECT_RECORD = _DATA_TYPE_FABRIC.CreateDataTypeMap(
      u'interface_object_record')

  _REGISTRATION_OBJECT_RECORD = _DATA_TYPE_FABRIC.CreateDataTypeMap(
      u'registration_object_record')

  _PROPERTY_TYPES = _DATA_TYPE_FABRIC.CreateDataTypeMap(u'cim_property_types')

  # A size of 0 indicates variable of size.
  _PROPERTY_TYPE_VALUE_SIZES = {
      0x00000002: 2,
      0x00000003: 4,
      0x00000004: 4,
      0x00000005: 8,
      0x00000008: 0,
      0x0000000b: 2,
      0x0000000d: 0,
      0x00000010: 1,
      0x00000011: 1,
      0x00000012: 2,
      0x00000013: 4,
      0x00000014: 8,
      0x00000015: 8,
      0x00000065: 0,
      0x00000066: 2,
      0x00000067: 2,
  }

  DATA_TYPE_CLASS_DEFINITION = u'CD'

  def __init__(self, data_type, data, debug=False, output_writer=None):
    """Initializes an object record.

    Args:
      data_type (str): object record data type.
      data (bytes): object record data.
      debug (Optional[bool]): True if debug information should be written.
      output_writer (Optional[OutputWriter]): output writer.
    """
    super(ObjectRecord, self).__init__(debug=debug, output_writer=output_writer)
    self.data = data
    self.data_type = data_type

  def _ReadClassDefinition(self, object_record_data):
    """Reads a class definition object record.

    Args:
      object_record_data (bytes): object record data.

    Raises:
      ParseError: if the object record cannot be read.
    """
    if self._debug:
      self._DebugPrintText(u'Reading class definition object record.\n')

    try:
      class_definition = self._CLASS_DEFINITION_OBJECT_RECORD.MapByteStream(
          object_record_data)
    except dtfabric_errors.MappingError as exception:
      raise errors.ParseError((
          u'Unable to parse class definition object record with '
          u'error: {0:s}').format(exception))

    try:
      utf16_stream = class_definition.super_class_name
      super_class_name = utf16_stream.decode(u'utf-16-le')
    except UnicodeDecodeError as exception:
      super_class_name = u''

    super_class_name_size = class_definition.super_class_name_size
    date_time = class_definition.date_time
    data_size = class_definition.data_size

    if self._debug:
      self._DebugPrintValueDecimal(
          u'Super class name size', super_class_name_size)

      self._DebugPrintValue(u'Super class name', super_class_name)

      value_string = u'{0!s}'.format(FromFiletime(date_time))
      self._DebugPrintValue(u'Unknown date and time', value_string)

      self._DebugPrintValueDecimal(u'Data size', data_size)

      self._DebugPrintData(u'Data', class_definition.data)

    self._ReadClassDefinitionHeader(class_definition.data)

    data_offset = 12 + (super_class_name_size * 2) + data_size
    if data_offset < len(object_record_data):
      if self._debug:
        self._DebugPrintData(u'Methods data', object_record_data[data_offset:])

      self._ReadClassDefinitionMethods(object_record_data[data_offset:])

  def _ReadClassDefinitionHeader(self, class_definition_data):
    """Reads a class definition header.

    Args:
      class_definition_data (bytes): class definition data.

    Raises:
      ParseError: if the class definition cannot be read.
    """
    if self._debug:
      self._DebugPrintText(u'Reading class definition header.\n')

    try:
      class_definition_header = self._CLASS_DEFINITION_HEADER.MapByteStream(
          class_definition_data)
    except dtfabric_errors.MappingError as exception:
      raise errors.ParseError((
          u'Unable to parse class definition header with error: {0:s}').format(
              exception))

    number_of_property_descriptors = (
        class_definition_header.number_of_property_descriptors)
    property_descriptors_array = (
        class_definition_header.property_descriptors)

    property_descriptors = []
    for index in range(number_of_property_descriptors):
      property_name_offset = property_descriptors_array[index].name_offset
      property_data_offset = property_descriptors_array[index].data_offset

      property_descriptor = PropertyDescriptor(
          property_name_offset, property_data_offset)
      property_descriptors.append(property_descriptor)

    if self._debug:
      self._DebugPrintValueDecimal(
          u'Unknown1', class_definition_header.unknown1)

      value_string = u'0x{0:08x}'.format(
          class_definition_header.class_name_offset)
      self._DebugPrintValue(u'Class name offset', value_string)

      self._DebugPrintValueDecimal(
          u'Default value size',
          class_definition_header.default_value_size)

      self._DebugPrintValue(
          u'Super class name block size',
          class_definition_header.super_class_name_block_size)

      super_class_name_block_data = (
          class_definition_header.super_class_name_block_data)
      self._DebugPrintData(
          u'Super class name block data', super_class_name_block_data)

      self._DebugPrintValueDecimal(
          u'Qualifiers block size',
          class_definition_header.qualifiers_block_size)

      qualifiers_block_data = (
          class_definition_header.qualifiers_block_data)
      self._DebugPrintData(u'Qualifiers block data', qualifiers_block_data)

      self._DebugPrintValueDecimal(
          u'Number of property descriptors', number_of_property_descriptors)

      for index, property_descriptor in enumerate(property_descriptors):
        description = u'Property descriptor: {0:d} name offset'.format(index)
        value_string = u'0x{0:08x}'.format(property_descriptor.name_offset)
        self._DebugPrintValue(description, value_string)

        description = u'Property descriptor: {0:d} definition offset'.format(
            index)
        value_string = u'0x{0:08x}'.format(
            property_descriptor.definition_offset)
        self._DebugPrintValue(description, value_string)

      default_value_data = (
          class_definition_header.default_value_data)
      self._DebugPrintData(u'Default value data', default_value_data)

      properties_block_size = (
          class_definition_header.properties_block_size)
      value_string = u'{0:d} (0x{1:08x})'.format(
          properties_block_size & 0x7fffffff, properties_block_size)
      self._DebugPrintValue(u'Properties block size', value_string)

      # TODO: refactor.
      if False:
        if class_definition_header.super_class_name_block_size > 4:
          super_class_name_block = (
              class_definition_header.super_class_name_block)

          value_string = u'0x{0:02x}'.format(
              super_class_name_block.super_class_name_flags)
          self._DebugPrintValue(u'Super class name flags', value_string)

          value_string = u'{0:s}'.format(
              super_class_name_block.uper_class_name_string)
          self._DebugPrintValue(u'Super class name string', value_string)

          self._DebugPrintValueDecimal(
              u'Super class name size',
              super_class_name_block.super_class_name_size)

      self._DebugPrintText(u'\n')

    properties_block_data = (
        class_definition_header.properties_block_data)
    self._ReadClassDefinitionProperties(
        properties_block_data, property_descriptors)

  def _ReadClassDefinitionMethods(self, class_definition_data):
    """Reads a class definition methods.

    Args:
      class_definition_data (bytes): class definition data.

    Raises:
      ParseError: if the class definition cannot be read.
    """
    if self._debug:
      self._DebugPrintText(u'Reading class definition methods.\n')

    try:
      class_definition_methods = self._CLASS_DEFINITION_METHODS.MapByteStream(
          class_definition_data)
    except dtfabric_errors.MappingError as exception:
      raise errors.ParseError((
          u'Unable to parse class definition methods with error: {0:s}').format(
              exception))

    methods_block_size = class_definition_methods.methods_block_size

    if self._debug:
      value_string = u'{0:d} (0x{1:08x})'.format(
          methods_block_size & 0x7fffffff, methods_block_size)
      self._DebugPrintValue(u'Methods block size', value_string)

      self._DebugPrintData(
          u'Methods block data',
          class_definition_methods.methods_block_data)

  def _ReadClassDefinitionProperties(
      self, properties_data, property_descriptors):
    """Reads class definition properties.

    Args:
      properties_data (bytes): class definition properties data.
      property_descriptors (list[PropertyDescriptor]): property descriptors.

    Raises:
      ParseError: if the class definition properties cannot be read.
    """
    if self._debug:
      self._DebugPrintText(u'Reading class definition properties.\n')

    if self._debug:
      self._DebugPrintData(u'Properties data', properties_data)

    for index, property_descriptor in enumerate(property_descriptors):
      name_offset = property_descriptor.name_offset & 0x7fffffff
      property_name_data = properties_data[name_offset:]

      try:
        property_name = self._PROPERTY_NAME.MapByteStream(property_name_data)
      except dtfabric_errors.MappingError as exception:
        raise errors.ParseError(
            u'Unable to parse property name with error: {0:s}'.format(
                exception))

      string_flags = property_name.string_flags

      # TODO: check if string flags is 0
      if self._debug:
        description = u'Property: {0:d} name string flags'.format(index)
        value_string = u'0x{0:02x}'.format(string_flags)
        self._DebugPrintValue(description, value_string)

        description = u'Property: {0:d} name string'.format(index)
        self._DebugPrintValue(description, property_name.string)

        self._DebugPrintText(u'\n')

      definition_offset = property_descriptor.definition_offset & 0x7fffffff
      property_definition_data = properties_data[definition_offset:]

      try:
        property_definition = self._PROPERTY_DEFINITION.MapByteStream(
            property_definition_data)
      except dtfabric_errors.MappingError as exception:
        raise errors.ParseError(
            u'Unable to parse property definition with error: {0:s}'.format(
                exception))

      if self._debug:
        property_type_string = self._RECORD_TYPE.GetName(
            property_definition.type)
        description = u'Property: {0:d} type'.format(index)
        value_string = u'0x{0:08x} ({1:s})'.format(
            property_definition.type, property_type_string or u'UNKNOWN')
        self._DebugPrintValue(description, value_string)

        description = u'Property: {0:d} index'.format(index)
        self._DebugPrintValueDecimal(
            description, property_definition.index)

        description = u'Property: {0:d} offset'.format(index)
        value_string = u'0x{0:08x}'.format(
            property_definition.offset)
        self._DebugPrintValue(description, value_string)

        description = u'Property: {0:d} level'.format(index)
        self._DebugPrintValueDecimal(
            description, property_definition.level)

        description = u'Property: {0:d} qualifiers block size'.format(index)
        self._DebugPrintValueDecimal(
            description, property_definition.qualifiers_block_size)

        description = u'Property: {0:d} qualifiers block data:'.format(index)
        self._DebugPrintData(
            description, property_definition.qualifiers_block_data)

      property_value_size = self._PROPERTY_TYPE_VALUE_SIZES.get(
          property_definition.type & 0x00001fff, None)
      # TODO: handle property value data.
      property_value_data = b''

      if property_value_size is not None:
        if self._debug:
          description = u'Property: {0:d} value size'.format(index)
          self._DebugPrintValueDecimal(description, property_value_size)

          # TODO: handle variable size value data.
          # TODO: handle array.
          description = u'Property: {0:d} value data:'.format(index)
          self._DebugPrintData(
              description, property_value_data[:property_value_size])

  def _ReadInterface(self, object_record_data):
    """Reads an interface object record.

    Args:
      object_record_data (bytes): object record data.

    Raises:
      ParseError: if the object record cannot be read.
    """
    if self._debug:
      self._DebugPrintText(u'Reading interface object record.\n')

    try:
      interface = self._INTERFACE_OBJECT_RECORD.MapByteStream(
          object_record_data)
    except dtfabric_errors.MappingError as exception:
      raise errors.ParseError(
          u'Unable to parse interace object record with error: {0:s}'.format(
              exception))

    try:
      utf16_stream = interface.string_digest_hash
      string_digest_hash = utf16_stream.decode(u'utf-16-le')
    except UnicodeDecodeError as exception:
      string_digest_hash = u''

    if self._debug:
      self._DebugPrintValue(u'String digest hash', string_digest_hash)

      value_string = u'{0!s}'.format(FromFiletime(interface.date_time1))
      self._DebugPrintValue(u'Unknown data and time1', value_string)

      value_string = u'{0!s}'.format(FromFiletime(interface.date_time2))
      self._DebugPrintValue(u'Unknown data and time2', value_string)

      self._DebugPrintValueDecimal(u'Data size', interface.data_size)

      self._DebugPrintText(u'\n')

      self._DebugPrintData(u'Data', interface.data)

  def _ReadRegistration(self, object_record_data):
    """Reads a registration object record.

    Args:
      object_record_data (bytes): object record data.

    Raises:
      ParseError: if the object record cannot be read.
    """
    if self._debug:
      self._DebugPrintText(u'Reading registration object record.\n')

    try:
      registration = self._REGISTRATION_OBJECT_RECORD.MapByteStream(
          object_record_data)
    except dtfabric_errors.MappingError as exception:
      raise errors.ParseError((
          u'Unable to parse registration object record with '
          u'error: {0:s}').format(exception))

    try:
      utf16_stream = registration.name_space_string
      name_space_string = utf16_stream.decode(u'utf-16-le')
    except UnicodeDecodeError as exception:
      name_space_string = u''

    try:
      utf16_stream = registration.class_name_string
      class_name_string = utf16_stream.decode(u'utf-16-le')
    except UnicodeDecodeError as exception:
      class_name_string = u''

    try:
      utf16_stream = registration.attribute_name_string
      attribute_name_string = utf16_stream.decode(u'utf-16-le')
    except UnicodeDecodeError as exception:
      attribute_name_string = u''

    try:
      utf16_stream = registration.attribute_value_string
      attribute_value_string = utf16_stream.decode(u'utf-16-le')
    except UnicodeDecodeError as exception:
      attribute_value_string = u''

    if self._debug:
      self._DebugPrintValueDecimal(
          u'Name space string size', registration.name_space_string_size)

      self._DebugPrintValue(u'Name space string', name_space_string)

      self._DebugPrintValueDecimal(
          u'Class name string size', registration.class_name_string_size)

      self._DebugPrintValue(u'Class name string', class_name_string)

      self._DebugPrintValueDecimal(
          u'Attribute name string size',
          registration.attribute_name_string_size)

      self._DebugPrintValue(u'Attribute name string', attribute_name_string)

      self._DebugPrintValueDecimal(
          u'Attribute value string size',
          registration.attribute_value_string_size)

      self._DebugPrintValue(u'Attribute value string', attribute_value_string)

      self._DebugPrintText(u'\n')

  def Read(self):
    """Reads an object record."""
    if self._debug:
      self._DebugPrintData(u'Object record data', self.data)

    if self._debug:
      if self.data_type == self.DATA_TYPE_CLASS_DEFINITION:
        self._ReadClassDefinition(self.data)
      elif self.data_type in (u'I', u'IL'):
        self._ReadInterface(self.data)
      elif self.data_type == u'R':
        self._ReadRegistration(self.data)


class ObjectsDataPage(data_format.BinaryDataFormat):
  """An objects data page.

  Attributes:
    page_offset (int): page offset or None.
  """

  _DATA_TYPE_FABRIC_DEFINITION = b'\n'.join([
      b'name: uint32',
      b'type: integer',
      b'attributes:',
      b'  format: unsigned',
      b'  size: 4',
      b'  units: bytes',
      b'---',
      b'name: cim_object_descriptor',
      b'type: structure',
      b'attributes:',
      b'  byte_order: little-endian',
      b'members:',
      b'- name: identifier',
      b'  data_type: uint32',
      b'- name: data_offset',
      b'  data_type: uint32',
      b'- name: data_size',
      b'  data_type: uint32',
      b'- name: data_checksum',
      b'  data_type: uint32',
  ])

  _DATA_TYPE_FABRIC = dtfabric_fabric.DataTypeFabric(
      yaml_definition=_DATA_TYPE_FABRIC_DEFINITION)

  _OBJECT_DESCRIPTOR = _DATA_TYPE_FABRIC.CreateDataTypeMap(
      u'cim_object_descriptor')

  _OBJECT_DESCRIPTOR_SIZE = _OBJECT_DESCRIPTOR.GetByteSize()

  _EMPTY_OBJECT_DESCRIPTOR = b'\x00' * _OBJECT_DESCRIPTOR_SIZE

  PAGE_SIZE = 8192

  def __init__(self, debug=False, output_writer=None):
    """Initializes an objects data page.

    Args:
      debug (Optional[bool]): True if debug information should be written.
      output_writer (Optional[OutputWriter]): output writer.
    """
    super(ObjectsDataPage, self).__init__(
        debug=debug, output_writer=output_writer)
    self._object_descriptors = []

    self.page_offset = None

  def _ReadObjectDescriptor(self, file_object):
    """Reads an object descriptor.

    Args:
      file_object (file): a file-like object.

    Returns:
      cim_object_descriptor: an object descriptor or None.

    Raises:
      ParseError: if the object descriptor cannot be read.
    """
    file_offset = file_object.tell()
    if self._debug:
      self._DebugPrintText(
          u'Reading object descriptor at offset: 0x{0:08x}\n'.format(
              file_offset))

    object_descriptor_data = file_object.read(self._OBJECT_DESCRIPTOR_SIZE)

    if self._debug:
      self._DebugPrintData(u'Object descriptor data', object_descriptor_data)

    # The last object descriptor (terminator) is filled with 0-byte values.
    if object_descriptor_data == self._EMPTY_OBJECT_DESCRIPTOR:
      return

    try:
      object_descriptor = self._OBJECT_DESCRIPTOR.MapByteStream(
          object_descriptor_data)
    except dtfabric_errors.MappingError as exception:
      raise errors.ParseError(
          u'Unable to parse object descriptor with error: {0:s}'.format(
              exception))

    if self._debug:
      value_string = u'0x{0:08x}'.format(object_descriptor.identifier)
      self._DebugPrintValue(u'Identifier', value_string)

      value_string = u'0x{0:08x} (0x{1:08x})'.format(
          object_descriptor.data_offset,
          file_offset + object_descriptor.data_offset)
      self._DebugPrintValue(u'Data offset', value_string)

      self._DebugPrintValueDecimal(u'Data size', object_descriptor.data_size)

      value_string = u'0x{0:08x}'.format(object_descriptor.data_checksum)
      self._DebugPrintValue(u'Checksum', value_string)

      self._DebugPrintText(u'\n')

    return object_descriptor

  def _ReadObjectDescriptors(self, file_object):
    """Reads object descriptors.

    Args:
      file_object (file): a file-like object.

    Raises:
      ParseError: if the object descriptor cannot be read.
    """
    while True:
      object_descriptor = self._ReadObjectDescriptor(file_object)
      if not object_descriptor:
        break

      self._object_descriptors.append(object_descriptor)

  def GetObjectDescriptor(self, record_identifier, data_size):
    """Retrieves a specific object descriptor.

    Args:
      record_identifier (int): object record identifier.
      data_size (int): object record data size.

    Returns:
      cim_object_descriptor: an object descriptor or None.
    """
    object_descriptor_match = None
    for object_descriptor in self._object_descriptors:
      if object_descriptor.identifier == record_identifier:
        object_descriptor_match = object_descriptor
        break

    if not object_descriptor_match:
      logging.warning(u'Object record data not found.')
      return

    if object_descriptor_match.data_size != data_size:
      logging.warning(u'Object record data size mismatch.')
      return

    return object_descriptor_match

  def ReadPage(self, file_object, file_offset, data_page=False):
    """Reads a page.

    Args:
      file_object (file): a file-like object.
      file_offset (int): offset of the page relative from the start of the file.
      data_page (Optional[bool]): True if the page is a data page.

    Raises:
      ParseError: if the page cannot be read.
    """
    file_object.seek(file_offset, os.SEEK_SET)

    if self._debug:
      self._DebugPrintText(
          u'Reading objects data page at offset: 0x{0:08x}\n'.format(
              file_offset))

    self.page_offset = file_offset

    if not data_page:
      self._ReadObjectDescriptors(file_object)

  def ReadObjectRecordData(self, file_object, data_offset, data_size):
    """Reads the data of an object record.

    Args:
      file_object (file): a file-like object.
      data_offset (int): offset of the object record data relative from
          the start of the page.
      data_size (int): object record data size.

    Returns:
      bytes: object record data.

    Raises:
      ParseError: if the object record cannot be read.
    """
    # Make the offset relative to the start of the file.
    file_offset = self.page_offset + data_offset

    file_object.seek(file_offset, os.SEEK_SET)

    if self._debug:
      self._DebugPrintText(
          u'Reading object record at offset: 0x{0:08x}\n'.format(file_offset))

    available_page_size = self.PAGE_SIZE - data_offset

    if data_size > available_page_size:
      read_size = available_page_size
    else:
      read_size = data_size

    return file_object.read(read_size)


class IndexBinaryTreeFile(object):
  """Index binary-tree (Index.btr) file."""

  def __init__(self, index_mapping_file, debug=False, output_writer=None):
    """Initializes an index binary-tree file.

    Args:
      index_mapping_file (MappingFile): an index mapping file.
      debug (Optional[bool]): True if debug information should be written.
      output_writer (Optional[OutputWriter]): output writer.
    """
    super(IndexBinaryTreeFile, self).__init__()
    self._debug = debug
    self._output_writer = output_writer

    self._file_object = None
    self._file_object_opened_in_object = False
    self._file_size = 0

    self._index_mapping_file = index_mapping_file
    self._first_mapped_page = None
    self._root_page = None

  def _GetPage(self, page_number):
    """Retrieves a specific page.

    Args:
      page_number (int): page number.

    Returns:
      IndexBinaryTreePage: an index binary-tree page or None.
    """
    file_offset = page_number * IndexBinaryTreePage.PAGE_SIZE
    if file_offset >= self._file_size:
      return

    # TODO: cache pages.
    return self._ReadPage(file_offset)

  def _ReadPage(self, file_offset):
    """Reads a page.

    Args:
      file_offset (int): offset of the page relative from the start of the file.

    Return:
      IndexBinaryTreePage: an index binary-tree page.
    """
    index_page = IndexBinaryTreePage(
        debug=self._debug, output_writer=self._output_writer)
    index_page.ReadPage(self._file_object, file_offset)
    return index_page

  def Close(self):
    """Closes the index binary-tree file."""
    if self._file_object_opened_in_object:
      self._file_object.close()
    self._file_object = None

  def GetFirstMappedPage(self):
    """Retrieves the first mapped page.

    Returns:
      IndexBinaryTreePage: an index binary-tree page or None.
    """
    if not self._first_mapped_page:
      page_number = self._index_mapping_file.mappings[0]

      index_page = self._GetPage(page_number)
      if not index_page:
        logging.warning((
            u'Unable to read first mapped index binary-tree page: '
            u'{0:d}.').format(page_number))
        return

      if index_page.page_type != 0xaddd:
        logging.warning(u'First mapped index binary-tree page type mismatch.')
        return

      self._first_mapped_page = index_page

    return self._first_mapped_page

  def GetMappedPage(self, page_number):
    """Retrieves a specific mapped page.

    Args:
      page_number (int): page number.

    Returns:
      IndexBinaryTreePage: an index binary-tree page or None.
    """
    mapped_page_number = self._index_mapping_file.mappings[page_number]

    index_page = self._GetPage(mapped_page_number)
    if not index_page:
      logging.warning(
          u'Unable to read index binary-tree mapped page: {0:d}.'.format(
              page_number))
      return

    return index_page

  def GetRootPage(self):
    """Retrieves the root page.

    Returns:
      IndexBinaryTreePage: an index binary-tree page or None.
    """
    if not self._root_page:
      first_mapped_page = self.GetFirstMappedPage()
      if not first_mapped_page:
        return

      page_number = self._index_mapping_file.mappings[
          first_mapped_page.root_page_number]

      index_page = self._GetPage(page_number)
      if not index_page:
        logging.warning(
            u'Unable to read index binary-tree root page: {0:d}.'.format(
                page_number))
        return

      self._root_page = index_page

    return self._root_page

  def Open(self, filename):
    """Opens the index binary-tree file.

    Args:
      filename (str): name of the file.
    """
    stat_object = os.stat(filename)
    self._file_size = stat_object.st_size

    self._file_object = open(filename, 'rb')
    self._file_object_opened_in_object = True

    if self._debug:
      file_offset = 0
      while file_offset < self._file_size:
        self._ReadPage(file_offset)
        file_offset += IndexBinaryTreePage.PAGE_SIZE


class MappingFile(data_format.BinaryDataFile):
  """Mappings (*.map) file.

  Attributes:
    data_size (int): data size of the mappings file.
    mapping (list[int]): mappings to page numbers in the index binary-tree
        or objects data file.
  """

  _DATA_TYPE_FABRIC_DEFINITION = b'\n'.join([
      b'name: uint32',
      b'type: integer',
      b'attributes:',
      b'  format: unsigned',
      b'  size: 4',
      b'  units: bytes',
      b'---',
      b'name: uint32le',
      b'type: integer',
      b'attributes:',
      b'  byte_order: little-endian',
      b'  format: unsigned',
      b'  size: 4',
      b'  units: bytes',
      b'---',
      b'name: cim_map_footer',
      b'type: structure',
      b'attributes:',
      b'  byte_order: little-endian',
      b'members:',
      b'- name: signature',
      b'  data_type: uint32',
      b'---',
      b'name: cim_map_header',
      b'type: structure',
      b'attributes:',
      b'  byte_order: little-endian',
      b'members:',
      b'- name: signature',
      b'  data_type: uint32',
      b'- name: format_version',
      b'  data_type: uint32',
      b'- name: number_of_pages',
      b'  data_type: uint32',
      b'---',
      b'name: cim_map_page_numbers',
      b'type: sequence',
      b'element_data_type: uint32le',
      b'number_of_elements: number_of_entries',
  ])

  _DATA_TYPE_FABRIC = dtfabric_fabric.DataTypeFabric(
      yaml_definition=_DATA_TYPE_FABRIC_DEFINITION)

  _UINT32LE = _DATA_TYPE_FABRIC.CreateDataTypeMap(u'uint32le')

  _UINT32LE_SIZE = _UINT32LE.GetByteSize()

  _HEADER_SIGNATURE = 0x0000abcd

  _FILE_HEADER = _DATA_TYPE_FABRIC.CreateDataTypeMap(u'cim_map_header')

  _FILE_HEADER_SIZE = _FILE_HEADER.GetByteSize()

  _FOOTER_SIGNATURE = 0x0000dcba

  _FILE_FOOTER = _DATA_TYPE_FABRIC.CreateDataTypeMap(u'cim_map_footer')

  _FILE_FOOTER_SIZE = _FILE_FOOTER.GetByteSize()

  _PAGE_NUMBERS = _DATA_TYPE_FABRIC.CreateDataTypeMap(u'cim_map_page_numbers')

  def __init__(self, debug=False, output_writer=None):
    """Initializes a mappings file.

    Args:
      debug (Optional[bool]): True if debug information should be written.
      output_writer (Optional[OutputWriter]): output writer.
    """
    super(MappingFile, self).__init__(
        debug=debug, output_writer=output_writer)
    self.data_size = 0
    self.mappings = []

  def _DebugPrintFileFooter(self, file_footer):
    """Prints file footer debug information.

    Args:
      file_footer (cim_map_footer): file footer.
    """
    value_string = u'0x{0:08x}'.format(file_footer.signature)
    self._DebugPrintValue(u'Signature', value_string)

    self._DebugPrintText(u'\n')

  def _DebugPrintFileHeader(self, file_header):
    """Prints file header debug information.

    Args:
      file_header (cim_map_header): file header.
    """
    value_string = u'0x{0:08x}'.format(file_header.signature)
    self._DebugPrintValue(u'Signature', value_string)

    value_string = u'0x{0:08x}'.format(file_header.format_version)
    self._DebugPrintValue(u'Format version', value_string)

    self._DebugPrintValueDecimal(
        u'Number of pages', file_header.number_of_pages)

    self._DebugPrintText(u'\n')

  def _DebugPrintPageNumber(
      self, description, page_number, unavailable_page_numbers=None):
    """Prints a page number debug information.

    Args:
      description (str): description.
      page_number (int): page number.
      unavailable_page_numbers (Optional[set[int]]): unavailable page numbers.
    """
    if not unavailable_page_numbers:
      unavailable_page_numbers = set()

    if page_number in unavailable_page_numbers:
      value_string = u'0x{0:08x} (unavailable)'.format(page_number)
    else:
      value_string = u'{0:d}'.format(page_number)

    self._DebugPrintValue(description, value_string)

  def _ReadFileFooter(self, file_object):
    """Reads the file footer.

    Args:
      file_object (file): file-like object.

    Raises:
      ParseError: if the file footer cannot be read.
    """
    file_offset = file_object.tell()

    file_footer = self._ReadStructure(
        file_object, file_offset, self._FILE_FOOTER_SIZE, self._FILE_FOOTER,
        u'file footer')

    if self._debug:
      self._DebugPrintFileFooter(file_footer)

    if file_footer.signature != self._FOOTER_SIGNATURE:
      raise errors.ParseError(
          u'Unsupported file footer signature: 0x{0:08x}'.format(
              file_footer.signature))

  def _ReadFileHeader(self, file_object, file_offset=0):
    """Reads the file header.

    Args:
      file_object (file): file-like object.
      file_offset (Optional[int]): offset of the mappings file header
          relative from the start of the file.

    Raises:
      ParseError: if the file header cannot be read.
    """
    file_header = self._ReadStructure(
        file_object, file_offset, self._FILE_HEADER_SIZE, self._FILE_HEADER,
        u'file header')

    if self._debug:
      self._DebugPrintFileHeader(file_header)

    if file_header.signature != self._HEADER_SIGNATURE:
      raise errors.ParseError(
          u'Unsupported file header signature: 0x{0:08x}'.format(
              file_header.signature))

  def _ReadMappings(self, file_object):
    """Reads the mappings.

    Args:
      file_object (file): file-like object.

    Raises:
      ParseError: if the mappings cannot be read.
    """
    file_offset = file_object.tell()
    mappings_array = self._ReadPageNumbersTable(
        file_object, file_offset, u'mappings')

    if self._debug:
      for index, page_number in enumerate(mappings_array):
        description = u'Mapping entry: {0:d} page number'.format(index)
        self._DebugPrintPageNumber(
            description, page_number,
            unavailable_page_numbers=set([0xffffffff]))

      self._DebugPrintText(u'\n')

    self.mappings = mappings_array

  def _ReadPageNumbersTable(self, file_object, file_offset, description):
    """Reads a page numbers table.

    Args:
      file_object (file): a file-like object.
      file_offset (int): offset of the data relative from the start of
          the file-like object.
      description (str): description of the page numbers table.

    Returns:
      tuple[int, ...]: page number array.

    Raises:
      ParseError: if the page numbers table cannot be read.
    """
    if self._debug:
      self._DebugPrintText(u'Reading {0:s} at offset: 0x{1:08x}\n'.format(
          description, file_offset))

    try:
      number_of_entries_data = file_object.read(self._UINT32LE_SIZE)
    except IOError as exception:
      raise errors.ParseError((
          u'Unable to read number of entries data at offset: 0x{0:08x} '
          u'with error: {1:s}').format(file_offset, exception))

    if len(number_of_entries_data) != self._UINT32LE_SIZE:
      raise errors.ParseError((
          u'Unable to read number of entries data at offset: 0x{0:08x} '
          u'with error: missing data').format(file_offset))

    try:
      number_of_entries = self._UINT32LE.MapByteStream(number_of_entries_data)
    except dtfabric_errors.MappingError as exception:
      raise errors.ParseError((
          u'Unable to parse number of entries at offset: 0x{0:08x} with error '
          u'error: {1:s}').format(file_offset, exception))

    if number_of_entries == 0:
      entries_data = b''
    else:
      entries_data_size = number_of_entries * self._UINT32LE_SIZE

      try:
        entries_data = file_object.read(entries_data_size)
      except IOError as exception:
        raise errors.ParseError((
            u'Unable to read entries data at offset: 0x{0:08x} with error: '
            u'{1:s}').format(file_offset, exception))

      if len(entries_data) != entries_data_size:
        raise errors.ParseError((
            u'Unable to read entries data at offset: 0x{0:08x} with error: '
            u'missing data').format(file_offset))

    if self._debug:
      data_description = u'{0:s} data'.format(description.title())
      self._DebugPrintData(data_description, b''.join([
          number_of_entries_data, entries_data]))

      self._DebugPrintValueDecimal(u'Number of entries', number_of_entries)

    if not entries_data:
      page_numbers = tuple()
    else:
      context = dtfabric_data_maps.DataTypeMapContext(values={
          u'number_of_entries': number_of_entries})

      try:
        page_numbers = self._PAGE_NUMBERS.MapByteStream(
            entries_data, context=context)

      except dtfabric_errors.MappingError as exception:
        raise errors.ParseError((
            u'Unable to parse entries data at offset: 0x{0:08x} with error: '
            u'{1:s}').format(file_offset, exception))

    return page_numbers

  def _ReadUnknownEntries(self, file_object):
    """Reads unknown entries.

    Args:
      file_object (file): file-like object.

    Raises:
      ParseError: if the unknown entries cannot be read.
    """
    file_offset = file_object.tell()
    unknown_entries_array = self._ReadPageNumbersTable(
        file_object, file_offset, u'unknown entries')

    if self._debug:
      for index, page_number in enumerate(unknown_entries_array):
        description = u'Unknown entry: {0:d} page number'.format(index)
        self._DebugPrintPageNumber(
            description, page_number,
            unavailable_page_numbers=set([0xffffffff]))

      self._DebugPrintText(u'\n')

  def ReadFileObject(self, file_object):
    """Reads a mappings file-like object.

    Args:
      file_object (file): file-like object.

    Raises:
      ParseError: if the file cannot be read.
    """
    file_offset = file_object.tell()

    self._ReadFileHeader(file_object, file_offset=file_offset)
    self._ReadMappings(file_object)
    self._ReadUnknownEntries(file_object)
    self._ReadFileFooter(file_object)

    self.data_size = file_object.tell() - file_offset


class ObjectsDataFile(data_format.BinaryDataFile):
  """An objects data (Objects.data) file."""

  _KEY_SEGMENT_SEPARATOR = u'\\'
  _KEY_VALUE_SEPARATOR = u'.'

  _KEY_VALUE_PAGE_NUMBER_INDEX = 1
  _KEY_VALUE_RECORD_IDENTIFIER_INDEX = 2
  _KEY_VALUE_DATA_SIZE_INDEX = 3

  def __init__(self, objects_mapping_file, debug=False, output_writer=None):
    """Initializes an objects data file.

    Args:
      objects_mapping_file (MappingFile): objects mapping file.
      debug (Optional[bool]): True if debug information should be written.
      output_writer (Optional[OutputWriter]): output writer.
    """
    super(ObjectsDataFile, self).__init__(
        debug=debug, output_writer=output_writer)
    self._objects_mapping_file = objects_mapping_file

  def _GetKeyValues(self, key):
    """Retrieves the key values from the key.

    Args:
      key (str): a CIM key.

    Returns:
      tuple[str, int, int, int]: name of the key, corresponding page number,
          record identifier and record data size or None.
    """
    _, _, key = key.rpartition(self._KEY_SEGMENT_SEPARATOR)

    if self._KEY_VALUE_SEPARATOR not in key:
      return

    key_values = key.split(self._KEY_VALUE_SEPARATOR)
    if not len(key_values) == 4:
      logging.warning(u'Unsupported number of key values.')
      return

    try:
      page_number = int(key_values[self._KEY_VALUE_PAGE_NUMBER_INDEX], 10)
    except ValueError:
      logging.warning(u'Unsupported key value page number.')
      return

    try:
      record_identifier = int(
          key_values[self._KEY_VALUE_RECORD_IDENTIFIER_INDEX], 10)
    except ValueError:
      logging.warning(u'Unsupported key value record identifier.')
      return

    try:
      data_size = int(key_values[self._KEY_VALUE_DATA_SIZE_INDEX], 10)
    except ValueError:
      logging.warning(u'Unsupported key value data size.')
      return

    return key_values[0], page_number, record_identifier, data_size

  def _GetPage(self, page_number, data_page=False):
    """Retrieves a specific page.

    Args:
      page_number (int): page number.
      data_page (Optional[bool]): True if the page is a data page.

    Returns:
      ObjectsDataPage: objects data page or None.
    """
    file_offset = page_number * ObjectsDataPage.PAGE_SIZE
    if file_offset >= self._file_size:
      return

    # TODO: cache pages.
    return self._ReadPage(file_offset, data_page=data_page)

  def _ReadPage(self, file_offset, data_page=False):
    """Reads a page.

    Args:
      file_offset (int): offset of the page relative from the start of the file.
      data_page (Optional[bool]): True if the page is a data page.

    Return:
      ObjectsDataPage: objects data page or None.

    Raises:
      ParseError: if the page cannot be read.
    """
    objects_page = ObjectsDataPage(
        debug=self._debug, output_writer=self._output_writer)
    objects_page.ReadPage(self._file_object, file_offset, data_page=data_page)
    return objects_page

  def GetMappedPage(self, page_number, data_page=False):
    """Retrieves a specific mapped page.

    Args:
      page_number (int): page number.
      data_page (Optional[bool]): True if the page is a data page.

    Returns:
      ObjectsDataPage: objects data page or None.
    """
    mapped_page_number = self._objects_mapping_file.mappings[page_number]

    objects_page = self._GetPage(mapped_page_number, data_page=data_page)
    if not objects_page:
      logging.warning(
          u'Unable to read objects data mapped page: {0:d}.'.format(
              page_number))
      return

    return objects_page

  def GetObjectRecordByKey(self, key):
    """Retrieves a specific object record.

    Args:
      key (str): a CIM key.

    Returns:
      ObjectRecord: an object record or None.

    Raises:
      ParseError: if the object record cannot be retrieved.
    """
    key, page_number, record_identifier, data_size = self._GetKeyValues(key)

    data_segments = []
    data_page = False
    data_segment_index = 0
    while data_size > 0:
      object_page = self.GetMappedPage(page_number, data_page=data_page)
      if not object_page:
        errors.ParseError(
            u'Unable to read objects record: {0:d} data segment: {1:d}.'.format(
                record_identifier, data_segment_index))

      if not data_page:
        object_descriptor = object_page.GetObjectDescriptor(
            record_identifier, data_size)

        data_offset = object_descriptor.data_offset
        data_page = True
      else:
        data_offset = 0

      data_segment = object_page.ReadObjectRecordData(
          self._file_object, data_offset, data_size)
      if not data_segment:
        errors.ParseError(
            u'Unable to read objects record: {0:d} data segment: {1:d}.'.format(
                record_identifier, data_segment_index))

      data_segments.append(data_segment)
      data_size -= len(data_segment)
      data_segment_index += 1
      page_number += 1

    data_type, _, _ = key.partition(u'_')
    object_record_data = b''.join(data_segments)

    return ObjectRecord(
        data_type, object_record_data, debug=self._debug,
        output_writer=self._output_writer)

  def ReadFileObject(self, file_object):
    """Reads an objects data file-like object.

    Args:
      file_object (file): file-like object.

    Raises:
      ParseError: if the file cannot be read.
    """
    self._file_object = file_object


class CIMRepository(object):
  """A CIM repository."""

  _DATA_TYPE_FABRIC_DEFINITION = b'\n'.join([
      b'name: uint32le',
      b'type: integer',
      b'attributes:',
      b'  byte_order: little-endian',
      b'  format: unsigned',
      b'  size: 4',
      b'  units: bytes',
  ])

  _DATA_TYPE_FABRIC = dtfabric_fabric.DataTypeFabric(
      yaml_definition=_DATA_TYPE_FABRIC_DEFINITION)

  _MAPPING_VER = _DATA_TYPE_FABRIC.CreateDataTypeMap(u'uint32le')

  _MAPPING_VER_SIZE = _MAPPING_VER.GetByteSize()

  def __init__(self, debug=False, output_writer=None):
    """Initializes a CIM repository.

    Args:
      debug (Optional[bool]): True if debug information should be written.
      output_writer (Optional[OutputWriter]): output writer.
    """
    super(CIMRepository, self).__init__()
    self._debug = debug
    self._index_binary_tree_file = None
    self._index_mapping_file = None
    self._objects_data_file = None
    self._objects_mapping_file = None
    self._output_writer = output_writer

  def _GetCurrentMappingFile(self, path):
    """Retrieves the current mapping file.

    Args:
      path (str): path to the CIM repository.

    Raises:
      ParseError: if the current mapping file cannot be read.
    """
    mapping_file_glob = glob.glob(
        os.path.join(path, u'[Mm][Aa][Pp][Pp][Ii][Nn][Gg].[Vv][Ee][Rr]'))

    active_mapping_file = 0
    if mapping_file_glob:
      with open(mapping_file_glob[0], 'rb') as file_object:
        active_mapping_file = self._ReadStructure(
            file_object, 0, self._MAPPING_VER_SIZE, self._MAPPING_VER,
            u'Mapping.ver')

      if self._debug:
        self._DebugPrintText(u'Active mapping file: {0:d}'.format(
            active_mapping_file))

    if mapping_file_glob:
      mapping_file_glob = glob.glob(os.path.join(
          path, u'[Mm][Aa][Pp][Pp][Ii][Nn][Gg]{0:d}.[Mm][Aa][Pp]'.format(
              active_mapping_file)))
    else:
      mapping_file_glob = glob.glob(os.path.join(
          path, u'[Mm][Aa][Pp][Pp][Ii][Nn][Gg][1-3].[Mm][Aa][Pp]'))

    # TODO: determine active mapping file for Windows Vista and later.
    for mapping_file_path in mapping_file_glob:
      if self._debug:
        self._DebugPrintText(u'Reading: {0:s}'.format(mapping_file_path))

      objects_mapping_file = MappingFile(
          debug=self._debug, output_writer=self._output_writer)
      objects_mapping_file.Open(mapping_file_path)

      index_mapping_file = MappingFile(
          debug=self._debug, output_writer=self._output_writer)
      index_mapping_file.Open(
          mapping_file_path, file_offset=objects_mapping_file.data_size)

  def _GetKeysFromIndexPage(self, index_page):
    """Retrieves the keys from an index page.

    Yields:
      str: a CIM key.
    """
    for key in index_page.keys:
      yield key

    for sub_page_number in index_page.sub_pages:
      sub_index_page = self._index_binary_tree_file.GetMappedPage(
          sub_page_number)
      for key in self._GetKeysFromIndexPage(sub_index_page):
        yield key

  def Close(self):
    """Closes the CIM repository."""
    if self._index_binary_tree_file:
      self._index_binary_tree_file.Close()
      self._index_binary_tree_file = None

    if self._index_mapping_file:
      self._index_mapping_file.Close()
      self._index_mapping_file = None

    if self._objects_data_file:
      self._objects_data_file.Close()
      self._objects_data_file = None

    if self._objects_mapping_file:
      self._objects_mapping_file.Close()
      self._objects_mapping_file = None

  def GetKeys(self):
    """Retrieves the keys.

    Yields:
      str: a CIM key.
    """
    if not self._index_binary_tree_file:
      return

    index_page = self._index_binary_tree_file.GetRootPage()
    for key in self._GetKeysFromIndexPage(index_page):
      yield key

  def GetObjectRecordByKey(self, key):
    """Retrieves a specific object record.

    Args:
      key (str): a CIM key.

    Returns:
      ObjectRecord: an object record or None.
    """
    if not self._objects_data_file:
      return

    return self._objects_data_file.GetObjectRecordByKey(key)

  def Open(self, path):
    """Opens the CIM repository.

    Args:
      path (str): path to the CIM repository.
    """
    # TODO: self._GetCurrentMappingFile(path)

    # Index mappings file.
    index_mapping_file_path = glob.glob(
        os.path.join(path, u'[Ii][Nn][Dd][Ee][Xx].[Mm][Aa][Pp]'))[0]

    if self._debug:
      self._DebugPrintText(u'Reading: {0:s}\n'.format(index_mapping_file_path))

    self._index_mapping_file = MappingFile(
        debug=self._debug, output_writer=self._output_writer)
    self._index_mapping_file.Open(index_mapping_file_path)

    # Index binary tree file.
    index_binary_tree_file_path = glob.glob(
        os.path.join(path, u'[Ii][Nn][Dd][Ee][Xx].[Bb][Tt][Rr]'))[0]

    if self._debug:
      self._DebugPrintText(u'Reading: {0:s}\n'.format(
          index_binary_tree_file_path))

    self._index_binary_tree_file = IndexBinaryTreeFile(
        self._index_mapping_file, debug=self._debug,
        output_writer=self._output_writer)
    self._index_binary_tree_file.Open(index_binary_tree_file_path)

    # Objects mappings file.
    objects_mapping_file_path = glob.glob(
        os.path.join(path, u'[Oo][Bb][Jj][Ee][Cc][Tt][Ss].[Mm][Aa][Pp]'))[0]

    if self._debug:
      self._DebugPrintText(u'Reading: {0:s}\n'.format(
          objects_mapping_file_path))

    self._objects_mapping_file = MappingFile(
        debug=self._debug, output_writer=self._output_writer)
    self._objects_mapping_file.Open(objects_mapping_file_path)

    # Objects data file.
    objects_data_file_path = glob.glob(
        os.path.join(path, u'[Oo][Bb][Jj][Ee][Cc][Tt][Ss].[Da][Aa][Tt][Aa]'))[0]

    if self._debug:
      self._DebugPrintText(u'Reading: {0:s}\n'.format(objects_data_file_path))

    self._objects_data_file = ObjectsDataFile(
        self._objects_mapping_file, debug=self._debug,
        output_writer=self._output_writer)
    self._objects_data_file.Open(objects_data_file_path)