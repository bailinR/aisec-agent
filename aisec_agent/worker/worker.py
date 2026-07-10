import logging
import time
from io import BytesIO
from logging.handlers import TimedRotatingFileHandler
from os import path
from typing import Dict, Any
from ugly_code.runit import Worker, Switch
from aisec_agent.config import LOG_CONFIG, DEFAULT_REDIS
from aisec_agent.logic.knowledge.logic import PreFileLogic
from aisec_agent.model.define import RedisQueue
from aisec_agent.model.oss import MinioClient

# wrapper = VibeVoiceWrapper(model_path=VOICE_MODEL_PATH)

class BaseWorker(Worker):
    def _init_log(self):
        cfg = LOG_CONFIG.copy()
        if "filename" in cfg:
            fn = cfg.pop("filename")
            cfg.pop("filemode", None)
            name, ext = path.splitext(path.basename(fn))
            target = "{}-{}{}".format(name, self.__class__.__name__, ext)
            fn = path.join(path.dirname(fn), target)
            hs = (TimedRotatingFileHandler(fn, when="D", encoding="utf-8", backupCount=3),)
            cfg.update(handlers=hs)
        logging.basicConfig(**cfg)

    def serve(self):
        self._init_log()


class FileProcessor:
    """文件处理器"""

    def __init__(self, batch_size=3):
        self.batch_size = batch_size
        self.queue = RedisQueue(**DEFAULT_REDIS)
        self.logger = logging.getLogger(self.__class__.__name__)
        self.minio_client = MinioClient()

    def process_file(self, task: Dict[str, Any]):
        """处理单个文件任务

        Args:
            task: 任务信息，包含file_id和topic

        Returns:
            dict: 处理结果
        """
        try:

            # 获取任务参数
            file_id = task.get('file_id')
            topic = task.get('topic')
            bucket = task.get('bucket')
            file_path = task.get('file_path')
            file_name = task.get('file_name')
            self.queue.update_status(file_id, {"progress": 0, "completed": False})
            file_data = self.minio_client.get_object(bucket, file_path)
            slice_count, has_header = PreFileLogic().pre_file(file_id=file_id, topic=topic, file_name=file_name,
                                                              file=BytesIO(file_data))
            self.queue.update_status(file_id, {"progress": 100, "completed": True, "slice_count": slice_count,
                                               "has_header": has_header})

        except Exception as e:
            # 更新处理状态为失败
            self.queue.update_status(file_id, {"progress": 100, "completed": False, "error": str(e)})
            self.logger.exception(f"处理文件失败: {str(e)}")

    def get_task(self):
        return self.queue.get(block=True, timeout=0, count=self.batch_size)


class FileProcessWorker(BaseWorker):
    """文件处理Worker"""

    def __init__(self, batch_size: int = 3):
        super().__init__(Switch("FileProcessor"))
        self.processor = FileProcessor()
        self.batch_size = batch_size

    def serve(self):
        """服务主循环"""
        super().serve()

        while self.switch:
            try:
                # 获取待处理的任务
                tasks = self.processor.get_task()

                if tasks:
                    # 串行处理任务以避免线程池序列化问题
                    for task in tasks:
                        self.processor.process_file(task)
                else:
                    time.sleep(1)  # 无任务时等待1秒

            except Exception as e:
                logging.exception(f"处理任务失败: {str(e)}")
                time.sleep(1)


class VibeVoiceProcessor:
    """文件处理器"""

    def __init__(self, batch_size=3):
        self.batch_size = batch_size
        self.queue = RedisQueue(queue_name="vibe_voice_queue", status_key="voice_status", **DEFAULT_REDIS)
        self.logger = logging.getLogger(self.__class__.__name__)
        self.minio_client = MinioClient()

    def get_task(self):
        return self.queue.get(block=True, timeout=0, count=self.batch_size)

    # def process_file(self, task: Dict[str, Any]):
    #     """处理单个文件任务
    #
    #     Args:
    #         task: 任务信息，包含file_id和topic
    #
    #     Returns:
    #         dict: 处理结果
    #     """
    #     task_id = task.get('task_id')
    #     text = task.get('text')
    #     bucket = task.get('bucket')
    #     voice_files = task.get('voice_files', [DEFAULT_VOICE_MP3, ], )
    #     self.queue.update_status(task_id, {"progress": 0, "completed": False})
    #     _n = f"{task_id}_{int(time.time())}.wav"
    #     tmp = generate_speech(
    #         text=text, voice_files=voice_files,
    #         wrapper=wrapper
    #     )
    #     self.minio_client.upload_object(bucket, _n, tmp, len(tmp))
    #     print("upload", _n)
    #     self.queue.update_status(task_id, {"progress": 100, "completed": True, "path": _n, })


class VibeVoiceProcessWorker(BaseWorker):
    """音频处理"""

    def __init__(self, batch_size: int = 3):
        super().__init__(Switch("VibeVoiceProcessor"))
        self.processor = VibeVoiceProcessor()
        self.batch_size = batch_size

    def serve(self):
        """服务主循环"""
        super().serve()

        while self.switch:
            try:
                # 获取待处理的任务
                tasks = self.processor.get_task()

                if tasks:
                    # 串行处理任务以避免线程池序列化问题
                    for task in tasks:
                        self.processor.process_file(task)
                else:
                    time.sleep(1)  # 无任务时等待1秒

            except Exception as e:
                logging.exception(f"处理任务失败: {str(e)}")
                time.sleep(1)
