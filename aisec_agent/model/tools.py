# import os
# import uuid
# from werkzeug.datastructures import FileStorage
# from aisec_agent.config import MINIO_CNF
# from aisec_agent.model.oss import MinioClient


class Singleton(type):
    """
    单例工具metaclass
    """
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super(Singleton, cls).__call__(*args, **kwargs)
        return cls._instances[cls]


#
# class UploadOSS:
#     """附件上传文件存储服务"""
#
#     def __init__(self):
#         self.minio = MinioClient()
#
#     def upload_oss(self, file: FileStorage) -> tuple:
#         filename = file.filename
#         _, ext = os.path.splitext(filename)
#         upload_filename = "{}/{}{}".format("img", uuid.uuid1().hex, ext)
#         file_size = get_file_size(file)
#         self.minio.upload_object(bucket_name=MINIO_CNF["bucket"], filename=upload_filename, file_data=file.stream,
#                                  file_size=file_size)
#         return filename, upload_filename