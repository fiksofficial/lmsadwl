"""Data models for LMSA API responses."""

from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class ROMInfo:
    name: str
    uri: str = ""
    md5: str = ""
    rom_type: int = 0
    un_zip: bool = False
    request_shared_user_id: bool = False
    publish_date: str = ""
    flash_flow: str = ""
    rom_match_id: str = ""

    @classmethod
    def from_dict(cls, d):
        return cls(
            name=d.get("name", ""),
            uri=d.get("uri", ""),
            md5=d.get("md5", ""),
            rom_type=d.get("type", 0),
            un_zip=d.get("unZip", False),
            request_shared_user_id=d.get("requestSharedUserId", False),
            publish_date=d.get("publishDate", ""),
            flash_flow=d.get("flashFlow", ""),
            rom_match_id=d.get("romMatchId", ""),
        )


@dataclass
class LookupResult:
    code: str
    desc: str
    roms: List[ROMInfo] = field(default_factory=list)
    flash_flow: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def found(self):
        return self.code == "0000" and len(self.roms) > 0

    @property
    def rescue_available(self):
        return self.code == "3040"

    @classmethod
    def from_response(cls, data):
        code = data.get("code", "?")
        desc = data.get("desc", "")
        content = data.get("content")
        roms = []
        flash_flow = ""
        if code == "0000" and content:
            items = content if isinstance(content, list) else [content]
            for item in items:
                rom_res = item.get("romResource", {})
                roms.append(ROMInfo(
                    name=rom_res.get("name", ""),
                    uri=rom_res.get("uri", ""),
                    md5=rom_res.get("md5", ""),
                    publish_date=rom_res.get("publishDate", ""),
                    flash_flow=item.get("flashFlow", ""),
                    rom_match_id=item.get("romMatchId", ""),
                ))
                if item.get("flashFlow"):
                    flash_flow = item["flashFlow"]
        return cls(code=code, desc=desc, roms=roms, flash_flow=flash_flow, raw=data)


@dataclass
class ModelInfo:
    category: str
    brand: str
    model_name: str
    market_name: str
    platform: str
    read_support: bool
    read_flow: str = ""

    @classmethod
    def from_dict(cls, d):
        return cls(
            category=d.get("category", ""),
            brand=d.get("brand", ""),
            model_name=d.get("modelName", ""),
            market_name=d.get("marketName", ""),
            platform=d.get("platform", ""),
            read_support=d.get("readSupport", False),
            read_flow=d.get("readFlow", ""),
        )


@dataclass
class DeviceInfo:
    serial: str = ""
    model: str = ""
    brand: str = ""
    fingerprint: str = ""
    incremental: str = ""
    sdk: str = ""
    display_id: str = ""
    carrier: str = ""
    market_name: str = ""
    device: str = ""
    adb_model: str = ""
    adb_product: str = ""

    @property
    def title(self):
        return f"{self.brand} {self.model}".strip()

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: d.get(k, "") for k in cls.__dataclass_fields__})
