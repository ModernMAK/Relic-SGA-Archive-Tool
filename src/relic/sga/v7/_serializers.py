from __future__ import annotations

from datetime import datetime, timezone
from typing import BinaryIO, Optional

from serialization_tools.structx import Struct

from relic.sga import _abc, _serializers as _s
from relic.sga._abc import Archive
from relic.sga.core import MagicWord, Version, MismatchError
from relic.sga.protocols import StreamSerializer, StorageType, VerificationType
from relic.sga.v7 import core

folder_layout = Struct("<I 4I")
folder_serializer = _s.FolderDefSerializer(folder_layout)

drive_layout = Struct("<64s 64s 5I")
drive_serializer = _s.DriveDefSerializer(drive_layout)

file_layout = Struct("<5I 2B 2I")


class FileDefSerializer(StreamSerializer[core.FileDef]):
    def __init__(self, layout: Struct):
        self.layout = layout

    def unpack(self, stream: BinaryIO):
        storage_type: int
        verification_type: int

        name_rel_pos, data_rel_pos, length, store_length, modified_seconds, verification_type, storage_type, crc, hash_pos = self.layout.unpack_stream(stream)

        modified = datetime.fromtimestamp(modified_seconds, timezone.utc)
        storage_type: StorageType = StorageType(storage_type)
        verification_type:VerificationType = VerificationType(verification_type)

        return core.FileDef(name_rel_pos, data_rel_pos, length, store_length, storage_type, modified, verification_type)

    def pack(self, stream: BinaryIO, value: core.FileDef) -> int:
        modified: int = int(value.modified.timestamp())
        storage_type = value.storage_type.value  # convert enum to value
        verification_type = value.verification.value # convert enum to value
        args = value.name_pos, value.data_pos, value.length_on_disk, value.length_in_archive, modified, verification_type, storage_type, value.crc, value.hash_pos
        return self.layout.pack_stream(stream, *args)


file_serializer = FileDefSerializer(file_layout)
toc_layout = Struct("<8I")
toc_header_serializer = _s.TocHeaderSerializer(toc_layout)


class APISerializers(_abc.APISerializer):
    def read(self, stream: BinaryIO, lazy: bool = False, decompress: bool = True) -> Archive:
        MagicWord.read_magic_word(stream)
        version = Version.unpack(stream)
        version.assert_version_matches(self.version)

        name: bytes
        name, header_size, data_pos, RSV_1 = self.layout.unpack_stream(stream)
        if RSV_1 != 1:
            raise MismatchError("Reserved Field", RSV_1, 1)
        header_pos = stream.tell()
        # stream.seek(header_pos)
        toc_header = self.TocHeader.unpack(stream)
        unk_a, block_size = self.metadata_layout.unpack_stream(stream)
        drive_defs, folder_defs, file_defs = _s._read_toc_definitions(stream, toc_header, header_pos, self.DriveDef, self.FolderDef, self.FileDef)
        names = _s._read_toc_names_as_count(stream, toc_header.name_info, header_pos)
        drives, files = _s._assemble_io_from_defs(drive_defs, folder_defs, file_defs, names, data_pos, stream)

        if not lazy:
            for file in files:
                lazy_info: Optional[_abc._FileLazyInfo] = file._lazy_info
                if lazy_info is None:
                    raise Exception("API read files, but failed to create lazy info!")
                else:
                    file.data = lazy_info.read(decompress)
                    file._lazy_info = None

        name: str = name.rstrip(b"").decode("utf-16-le")
        metadata = core.ArchiveMetadata(unk_a,block_size)

        return Archive(name, metadata, drives)

    def write(self, stream: BinaryIO, archive: Archive) -> int:
        raise NotImplementedError

    def __init__(self):
        self.DriveDef = drive_serializer
        self.FolderDef = folder_serializer
        self.FileDef = file_serializer
        self.TocHeader = toc_header_serializer
        self.version = core.version
        self.layout = Struct("<128s 3I")
        self.metadata_layout = Struct("<2I")