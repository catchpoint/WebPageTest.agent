# -*- coding: utf-8 -*-
# Generated by the protocol buffer compiler.  DO NOT EDIT!
# source: all_pages.proto
# Protobuf Python Version: 5.26.1
"""Generated protocol buffer code."""
from google.protobuf import descriptor as _descriptor
from google.protobuf import descriptor_pool as _descriptor_pool
from google.protobuf import symbol_database as _symbol_database
from google.protobuf.internal import builder as _builder
# @@protoc_insertion_point(imports)

_sym_db = _symbol_database.Default()




DESCRIPTOR = _descriptor_pool.Default().AddSerializedFile(b'\n\x0f\x61ll_pages.proto\"\xad\x03\n\nPageRecord\x12\x0c\n\x04\x64\x61te\x18\x01 \x02(\x05\x12\x0e\n\x06\x63lient\x18\x02 \x02(\t\x12\x0c\n\x04page\x18\x03 \x02(\t\x12\x14\n\x0cis_root_page\x18\x04 \x02(\x08\x12\x11\n\troot_page\x18\x05 \x02(\t\x12\x0c\n\x04rank\x18\x06 \x01(\x05\x12\r\n\x05wptid\x18\x07 \x01(\t\x12\x0f\n\x07payload\x18\x08 \x01(\t\x12\x0f\n\x07summary\x18\t \x01(\t\x12\x16\n\x0e\x63ustom_metrics\x18\n \x01(\t\x12\x12\n\nlighthouse\x18\x0b \x01(\t\x12%\n\x08\x66\x65\x61tures\x18\x0c \x03(\x0b\x32\x13.PageRecord.Feature\x12,\n\x0ctechnologies\x18\r \x03(\x0b\x32\x16.PageRecord.Technology\x12\x10\n\x08metadata\x18\x0e \x01(\t\x1a\x34\n\x07\x46\x65\x61ture\x12\x0f\n\x07\x66\x65\x61ture\x18\x01 \x01(\t\x12\n\n\x02id\x18\x02 \x01(\t\x12\x0c\n\x04type\x18\x03 \x01(\t\x1a\x42\n\nTechnology\x12\x12\n\ntechnology\x18\x01 \x01(\t\x12\x12\n\ncategories\x18\x02 \x03(\t\x12\x0c\n\x04info\x18\x03 \x03(\t')

_globals = globals()
_builder.BuildMessageAndEnumDescriptors(DESCRIPTOR, _globals)
_builder.BuildTopDescriptorsAndMessages(DESCRIPTOR, 'all_pages_pb2', _globals)
if not _descriptor._USE_C_DESCRIPTORS:
  DESCRIPTOR._loaded_options = None
  _globals['_PAGERECORD']._serialized_start=20
  _globals['_PAGERECORD']._serialized_end=449
  _globals['_PAGERECORD_FEATURE']._serialized_start=329
  _globals['_PAGERECORD_FEATURE']._serialized_end=381
  _globals['_PAGERECORD_TECHNOLOGY']._serialized_start=383
  _globals['_PAGERECORD_TECHNOLOGY']._serialized_end=449
# @@protoc_insertion_point(module_scope)
