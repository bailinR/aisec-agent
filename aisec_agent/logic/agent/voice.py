import os
from os import path

from vibevoice_wrapper import generate_speech, VibeVoiceWrapper

from aisec_agent.config import VOICE_MODEL_PATH
from aisec_agent.config import VOICE_MP3_PATH
from aisec_agent.logic._tools import aided_chat
from aisec_agent.logic.exc import MessageException
from aisec_agent.model.define import BaseHandler
from aisec_agent.model.llm_typing import VoiceLLMInputFormTwo
from aisec_agent.model.oss import MinioClient
from aisec_agent.model.prompts import VoicePromt


class VoiceAgents(BaseHandler):
    wrapper = VibeVoiceWrapper(model_path=VOICE_MODEL_PATH)
    minio_client = MinioClient()

    def text(self, text, voice_files, **kwargs) -> str:
        if len(voice_files) == 2:
            _m = VoiceLLMInputFormTwo
        else:
            return f"Speaker 1: {text}"
        data = aided_chat(VoicePromt(text, ), "", _m, **kwargs)
        tmp = []
        for row in data:
            tmp.append(
                f'Speaker 1: {row["speaker1"]}\n Speaker 2: {row["speaker2"]}\n'
            )
        return "".join(tmp)

    def sync(self, bucket, _n, text, voice_files):
        tmp = generate_speech(
            text=text, voice_files=voice_files,
            wrapper=self.wrapper
        )
        self.minio_client.upload_object(bucket, _n, tmp, file_size=len(tmp))

    def voice_files_sync(self, names: list[str]):
        if len(names) > 2:
            raise MessageException("不能超过两个mp3名称")
        data = []
        for name in names:
            if path.exists(path.join(VOICE_MP3_PATH, name)):
                data.append(path.join(VOICE_MP3_PATH, name))
        if len(data) != len(names):
            raise MessageException("MP3 名称异常 请检查输入")
        return data

    def list_mp3(self):
        mp3_result = []
        for _, _, files in os.walk(VOICE_MP3_PATH):
            for row in files:

                file_ext = path.splitext(row)[-1].lower()

                if file_ext in [".wav", ".mp3"]:
                    file_name = path.basename(row)  # 获取文件名（含后缀）
                    mp3_result.append({"key": file_name, "value": file_name})

        return mp3_result
