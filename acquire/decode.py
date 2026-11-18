import json
import xml.etree.ElementTree as ET

def decode(body, headers):
    root=ET.fromstring(body); return [{'key': x.attrib['id'], 'value': {'type': x.tag.rsplit('}',1)[-1], **x.attrib}} for x in root.findall('.//{urn:hall:enumeration}Items/*')]
