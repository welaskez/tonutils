from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, parse_qs, urlencode

from nacl.signing import VerifyKey

from ..models import Account, DeviceInfo, TonProof
from ..utils.exceptions import TonConnectError
from ..utils.logger import logger


@dataclass
class WalletApp:
    """
    Represents a wallet application with relevant metadata and URLs.
    """
    app_name: str
    name: str
    image: Optional[str] = None
    bridge_url: Optional[str] = None
    tondns: Optional[str] = None
    about_url: Optional[str] = None
    universal_url: Optional[str] = None
    deep_link: Optional[str] = None
    platforms: Optional[List[str]] = None

    def __repr__(self) -> str:
        return (
            f"WalletApp(app_name={self.app_name}, "
            f"name={self.name}, "
            f"image={self.image}, "
            f"bridge_url={self.bridge_url}, "
            f"tondns={self.tondns}, "
            f"about_url={self.about_url}, "
            f"universal_url={self.universal_url}, "
            f"deep_link={self.deep_link}, "
            f"platforms={self.platforms})"
        )

    @property
    def direct_url(self) -> Optional[str]:
        """
        Converts the universal URL to a direct URL by modifying query parameters.

        :return: The direct URL as a string.
        """
        if self.universal_url is None:
            return None
        return self.universal_url_to_direct_url(self.universal_url)

    @staticmethod
    def universal_url_to_direct_url(universal_url: str) -> str:
        """
        Transforms a universal URL into a direct URL by adjusting its path and query parameters.

        :param universal_url: The universal URL to convert.
        :return: The converted direct URL.
        """
        parsed = urlparse(universal_url)
        query_dict = parse_qs(parsed.query)

        # Remove the 'attach' parameter if present and modify the path
        if query_dict.pop("attach", None) is not None:
            new_path = parsed.path.rstrip("/")
            if not new_path.endswith("/start"):
                new_path += "/start"
            parsed = parsed._replace(path=new_path)

        # Reconstruct the query string without the removed parameters
        new_query = urlencode(query_dict, doseq=True)
        parsed = parsed._replace(query=new_query)
        return parsed.geturl()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> WalletApp:
        """
        Creates a WalletApp instance from a dictionary containing wallet data.

        :param data: A dictionary with wallet information.
        :return: An instance of WalletApp.
        """
        return cls(
            app_name=data.get("app_name"),  # type: ignore
            name=data.get("name"),  # type: ignore
            image=data.get("image"),
            bridge_url=data.get("bridge_url"),
            tondns=data.get("tondns"),
            about_url=data.get("about_url"),
            universal_url=data.get("universal_url"),
            deep_link=data.get("deepLink"),
            platforms=data.get("platforms"),
        )

    def to_dict(self) -> Dict[str, Any]:
        """
        Converts the WalletApp instance into a dictionary format suitable for serialization.

        :return: A dictionary representation of the WalletApp.
        """
        return {
            "app_name": self.app_name,
            "name": self.name,
            "image": self.image,
            "bridge_url": self.bridge_url,
            "tondns": self.tondns,
            "about_url": self.about_url,
            "universal_url": self.universal_url,
            "deepLink": self.deep_link,
            "platforms": self.platforms,
        }


@dataclass
class WalletInfo:
    """
    Represents detailed information about a connected wallet, including device info,
    account details, provider, and TON proof.
    """
    device: Optional[DeviceInfo] = None
    provider: str = field(default="http")
    account: Optional[Account] = None
    ton_proof: Optional[TonProof] = None

    def verify_proof(self, src_payload: Optional[str] = None) -> bool:
        """
        Verifies the TON proof against the wallet's account and device information.

        :param src_payload: Optional payload to include in the verification message.
                            If not provided, the payload from the TON proof is used.
        :return: True if the proof is valid and unexpired, False otherwise.
        """
        if self.ton_proof is None or self.account is None:
            return False

        wc, whash = self.account.address.wc, self.account.address.hash_part

        # Construct the message for verification
        message = bytearray()
        message.extend("ton-proof-item-v2/".encode())
        message.extend(wc.to_bytes(4, "little"))
        message.extend(whash)
        message.extend(self.ton_proof.domain_len.to_bytes(4, "little"))
        message.extend(self.ton_proof.domain_val.encode())
        message.extend(self.ton_proof.timestamp.to_bytes(8, "little"))
        message.extend((src_payload or self.ton_proof.payload).encode())

        # Construct the signature message
        signature_message = bytearray()
        signature_message.extend(bytes.fromhex("ffff"))
        signature_message.extend("ton-connect".encode())
        signature_message.extend(hashlib.sha256(message).digest())

        # Retrieve and validate the public key
        public_key = self.account.public_key
        if isinstance(public_key, str):
            try:
                public_key_bytes = bytes.fromhex(public_key)
            except ValueError:
                logger.debug("Public key is not a valid hex string.")
                return False
        elif isinstance(public_key, bytes):
            public_key_bytes = public_key
        else:
            logger.debug("Public key is neither str nor bytes.")
            return False

        # Verify the signature
        try:
            verify_key = VerifyKey(public_key_bytes)
            verify_key.verify(
                hashlib.sha256(signature_message).digest(),
                self.ton_proof.signature,
            )
            logger.debug("Proof is ok!")
            return True
        except (Exception,):
            logger.debug("Proof is invalid!")
        return False

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> WalletInfo:
        """
        Creates a WalletInfo instance from a payload dictionary.

        :param payload: A dictionary containing wallet payload data.
        :raises TonConnectError: If required items are missing in the payload.
        :return: An instance of WalletInfo.
        """
        items = payload.get("items")
        if not items:
            raise TonConnectError("items not contains in payload")

        wallet = cls()
        for item in items:
            item_name = item.pop("name")
            if item_name == "ton_addr":
                wallet.account = Account.from_dict(item)
            elif item_name == "ton_proof":
                wallet.ton_proof = TonProof.from_dict(item)

        if not wallet.account:
            raise TonConnectError("ton_addr not contains in items")

        device_info = payload.get("device")
        if device_info:
            wallet.device = DeviceInfo.from_dict(device_info)

        return wallet

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> WalletInfo:
        """
        Creates a WalletInfo instance from a dictionary containing wallet data.

        :param data: A dictionary with wallet information.
        :return: An instance of WalletInfo.
        """
        device_data = data.get("device")
        account_data = data.get("account")
        ton_proof_data = data.get("ton_proof")

        device_obj = None
        if device_data is not None:
            device_obj = DeviceInfo.from_dict(device_data)

        account_obj = None
        if account_data is not None:
            account_obj = Account.from_dict(account_data)

        ton_proof_obj = None
        if ton_proof_data is not None:
            ton_proof_obj = TonProof.from_dict(ton_proof_data)

        return cls(
            device=device_obj,
            provider=data.get("provider", "http"),
            account=account_obj,
            ton_proof=ton_proof_obj,
        )

    def to_dict(self) -> Dict[str, Any]:
        """
        Converts the WalletInfo instance into a dictionary format suitable for serialization.

        :return: A dictionary representation of the WalletInfo.
        """
        return {
            "device": self.device.to_dict() if self.device else None,
            "provider": self.provider,
            "account": self.account.to_dict() if self.account else None,
            "ton_proof": self.ton_proof.to_dict() if self.ton_proof else None,
        }