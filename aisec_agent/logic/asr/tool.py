from funasr import AutoModel
from funasr.utils.postprocess_utils import rich_transcription_postprocess
from werkzeug.datastructures import FileStorage
from aisec_agent.config import IIC_MODEL
from aisec_agent.model.define import BaseHandler


class ASRModelTools(BaseHandler):
    def _init(self):
        self.model = AutoModel(
            model=IIC_MODEL,
            trust_remote_code=True,
            vad_model="fsmn-vad",
            vad_kwargs={"max_single_segment_time": 30000},
            device="cuda:0",
        )

    def to_text(self, voice_file: FileStorage):
        res = self.model.generate(
            input=voice_file,
            cache={},
            language="auto",
            use_itn=True,
            batch_size_s=60,
            merge_vad=True,  #
            merge_length_s=15,
        )
        text = rich_transcription_postprocess(res[0]["text"])
        return text