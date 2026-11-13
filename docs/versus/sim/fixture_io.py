"""Lossless full-grid fixture encoding, compressed to keep the public kit small."""
import base64
import json
import zlib


def pack_rows(rows):
    """Store field names once and compress repeated planned-grid cells losslessly."""
    columns=sorted({key for row in rows for key in row})
    values=[[row.get(k) for k in columns] for row in rows]
    raw=json.dumps(values,separators=(',',':'),allow_nan=False).encode('utf-8')
    return {'columns':columns,'row_count':len(rows),
            'zlib_base85':base64.b85encode(zlib.compress(raw,9)).decode('ascii')}


def unpack_rows(table):
    """Recover exact reply dictionaries and order without rerunning a generator."""
    raw=zlib.decompress(base64.b85decode(table['zlib_base85'].encode('ascii')))
    values=json.loads(raw)
    if len(values)!=table['row_count']:
        raise ValueError('fixture row count mismatch')
    return [{k:v for k,v in zip(table['columns'],row) if v is not None} for row in values]
