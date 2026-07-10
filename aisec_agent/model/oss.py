import json
import logging
from datetime import timedelta
from typing import IO
from minio import Minio
from minio.error import MinioException, S3Error

__all__ = ("MinioClient",)

from aisec_agent.config import MINIO_CNF


class MinioClient:
    def __init__(self):
        self.client = Minio(
            MINIO_CNF["endpoint"],
            access_key=MINIO_CNF["access_key"],
            secret_key=MINIO_CNF["secret_key"],
            secure=MINIO_CNF["secure"]
        )

    def __enter__(self):
        return self.client

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def ensure_bucket_exists(self, bucket_name: str, is_public: bool = False):
        """
        确保 bucket 存在，不存在则自动创建
        Args:
            bucket_name: 桶名称
            is_public: 是否设为公共可访问
        """
        try:
            if not self.client.bucket_exists(bucket_name):
                self.client.make_bucket(bucket_name)
                logging.info(f"Bucket {bucket_name} 创建成功")

                if is_public:
                    policy = {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Principal": "*",  # 允许所有人访问
                                "Action": ["s3:GetObject"],
                                "Resource": [f"arn:aws:s3:::{bucket_name}/*"]
                            }
                        ]
                    }
                    self.client.set_bucket_policy(bucket_name, json.dumps(policy))
                    logging.info(f"Bucket {bucket_name} 设为公共可访问")
            else:
                logging.debug(f"Bucket {bucket_name} 已存在")
        except MinioException as e:
            logging.error(f"检查或创建 bucket {bucket_name} 失败: {str(e)}")
            raise e


    def upload_file(self, bucket_name: str, oss_filename: str, file_path: str):
        try:
            result = self.client.fput_object(bucket_name, oss_filename, file_path)
            logging.info(
                "created {0} object; etag: {1}, version-id: {2}".format(
                    oss_filename, result.etag, result.version_id,
                ),
            )
            return result
        except MinioException as e:
            logging.error('upload file to minio error!, errmsg: {}'.format(str(e)))
            raise e

    def upload_object(self, bucket_name: str, filename: str, file_data: IO[bytes], file_size: int,
                      content_type: str = "application/octet-stream"):
        try:
            result = self.client.put_object(bucket_name, filename, file_data, file_size, content_type)
            logging.info(
                "created {0} object; etag: {1}, version-id: {2}".format(
                    filename, result.etag, result.version_id,
                ),
            )
            return result
        except MinioException as e:
            logging.error('upload object to minio error!, errmsg: {}'.format(str(e)))
            raise e

    def get_object(self, bucket_name: str, filename: str):
        try:
            # 获取对象
            response = self.client.get_object(bucket_name, filename)
            
            # 读取数据
            data = response.read()
            
            # 关闭响应
            response.close()
            response.release_conn()
            
            # 记录日志
            logging.info(f"成功从Minio获取文件: {filename}, 大小: {len(data)} bytes")
            
            return data
        except Exception as e:
            logging.error(f"从Minio获取文件失败: {str(e)}", exc_info=True)
            raise e

    def get_sign_url(self, bucket_name: str, filename: str, expires: int = timedelta(seconds=600)):
        return self.client.presigned_get_object(bucket_name, filename, expires)

    def make_bucket(self, bucket_name: str, is_public: bool = False):
        # TODO: 检查bucket是否存在
        if not self.client.bucket_exists(bucket_name):
            self.client.make_bucket(bucket_name)
        if is_public:
            policy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": "*",  # 允许所有人访问
                        "Action": ["s3:GetObject"],
                        "Resource": [f"arn:aws:s3:::{bucket_name}/*"]
                    }
                ]
            }

            self.client.set_bucket_policy(bucket_name, json.dumps(policy))
