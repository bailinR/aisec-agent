import requests, json
from typing import Iterator, Tuple
from aisec_agent.config import DEEP_SEARCH_CONF
from aisec_agent.logic._tools import aided_chat
from aisec_agent.model.define import BaseHandler
from aisec_agent.model.llm_typing import FinishSearchInputModel, FinishSearchInfoModel
from aisec_agent.model.prompts import FinishSearchInput, FinishSearchInfo


class BochaSearch:
    def parse_response_stream(self, resp: Iterator[bytes]) -> Iterator[str]:
        """将stream的sse event bytes数据解析成line格式"""
        for line in resp:
            if line:
                if line.startswith(b"data:"):
                    _line = line[len(b"data:"):]
                    _line = _line.decode("utf-8")
                else:
                    _line = line.decode("utf-8")
                yield _line

    def ai_search(self, query: str, search_conf: dict, freshness: str = "noLimit", answer: bool = False,
                        stream: bool = False):
        """ 博查AI搜索 """
        data = {"query": query, "freshness": freshness, "answer": answer, "stream": stream}
        resp = requests.post(search_conf["url"], headers={"Authorization": f"Bearer {search_conf['key']}"},
                             json=data, stream=stream)
        if stream:
            return (json.loads(line) for line in self.parse_response_stream(resp.iter_lines()))
        else:
            if resp.status_code == 200:
                return resp.json()
            else:
                raise "bocha ai search api error."


class SearchFuncs(BaseHandler):
    """为接入多搜索源做准备"""
    def _init(self):
        self.bocha = BochaSearch()

    def bocha_search(self, search_input: str, search_conf: dict) -> dict:
        """进阶一些可以直接写个pydantic的基类,更优雅,但是我们不要优雅"""
        return json.loads(self.bocha.ai_search(search_input, search_conf)["messages"][0]["content"])["value"]


class DeepSearchTool(BaseHandler):
    def convert_to_markdown(self, data: dict) -> dict:
        """将提供的字典数据转换为Markdown格式的字符串。可能需要单独做样式所以独立出来"""
        markdown_string = ""
        title, url = '', ''
        if 'name' in data and 'url' in data:
            title = data['name']
            url = data['url']
            markdown_string += f"### [{title}]({url})\n\n"

        if 'siteName' in data:
            markdown_string += f"> **来源**：{data['siteName']}\n"
        if 'datePublished' in data:
            date_part = data['datePublished'].split('T')[0]
            markdown_string += f"> **发布日期**：{date_part}\n"
        if 'summary' in data:
            markdown_string += f"> **摘要**：{data['summary']}\n"
        markdown_string += "\n---\n"

        return {"title": title, "url": url, "content": markdown_string}

    def search_logic(self, prompt: str, question: str, search_max_token: int = 10000) -> Tuple:
        """用于在线搜索的逻辑，最终输出搜索结果的markdown列表"""
        finish_input_search_prompt = FinishSearchInput(prompt)
        search_input = aided_chat(finish_input_search_prompt, question, FinishSearchInputModel)["search_input"]
        search_results = SearchFuncs().bocha_search(search_input, DEEP_SEARCH_CONF)
        search_result = [self.convert_to_markdown(row) for row in search_results]
        search_result_show = {"id": f"search_0", "title": "搜索结果", "content": search_result, "contentType": "deepsearch"}
        if len(search_result) > search_max_token:
            finish_search_prompt = FinishSearchInfo(search_result)
            q = f"用户问题:{question}\n\n搜索输入:{search_input}"
            search_result = aided_chat(finish_search_prompt, q, FinishSearchInfoModel)["search_result"]
        return search_result, search_result_show

