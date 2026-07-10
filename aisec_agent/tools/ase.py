import base64
import hashlib
from datetime import datetime

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad


class PasswordCrypto:
    def __init__(self, ak, sk, date:datetime=None):
        if date is None:
            date = datetime.now()
        date_str = f'{int(date.strftime("%Y%m%d")) >> 1}'
        combined = ak + date_str + sk
        self.key = hashlib.sha256(combined.encode('utf-8')).digest()

    def encrypt(self, password):
        cipher = AES.new(self.key, AES.MODE_CBC)
        ct_bytes = cipher.encrypt(pad(password.encode('utf-8'), AES.block_size))
        iv = cipher.iv
        return base64.b64encode(iv + ct_bytes).decode('utf-8')

    def decrypt(self, encrypted):
        try:
            encrypted = base64.b64decode(encrypted)
            iv = encrypted[:AES.block_size]
            ct = encrypted[AES.block_size:]
            cipher = AES.new(self.key, AES.MODE_CBC, iv=iv)
            pt = unpad(cipher.decrypt(ct), AES.block_size)
            return pt.decode('utf-8')
        except Exception as e:
            raise ValueError("Decryption failed: " + str(e))