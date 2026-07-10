import tempfile
import unittest
import json
from pathlib import Path

from aisec_agent.logic.project_materials import (
    DEFAULT_KNOWLEDGE_BASE_NAME,
    RECRUITMENT_KNOWLEDGE_BASE_NAME,
    ProjectMaterialStore,
)


class ProjectMaterialStoreTest(unittest.TestCase):
    def test_store_creates_project_folder_and_scene_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            projects = store.list_projects()

            self.assertEqual(len(projects), 1)
            project_id = projects[0]["project_id"]
            self.assertTrue((Path(temp_dir) / project_id / "project.json").exists())
            self.assertTrue((Path(temp_dir) / project_id / "global_prompt.md").exists())
            self.assertTrue((Path(temp_dir) / project_id / "scenes" / "health_presales" / "prompt.md").exists())
            self.assertTrue((Path(temp_dir) / project_id / "scenes" / "health_presales" / "materials.md").exists())
            self.assertTrue((Path(temp_dir) / project_id / "knowledge" / "manifest.json").exists())
            self.assertTrue(
                (
                    Path(temp_dir)
                    / project_id
                    / "knowledge"
                    / "files"
                    / DEFAULT_KNOWLEDGE_BASE_NAME
                    / "公司定位"
                    / "默认板块"
                    / "公司定位.md"
                ).exists()
            )
            self.assertTrue(
                (
                    Path(temp_dir)
                    / project_id
                    / "knowledge"
                    / "files"
                    / DEFAULT_KNOWLEDGE_BASE_NAME
                    / "三大核心业务板块"
                    / "默认板块"
                    / "大健康医疗板块.md"
                ).exists()
            )
            self.assertTrue(
                (
                    Path(temp_dir)
                    / project_id
                    / "knowledge"
                    / "files"
                    / RECRUITMENT_KNOWLEDGE_BASE_NAME
                    / "岗位资料"
                    / "AI项目经理"
                    / "AI项目经理岗位说明.md"
                ).exists()
            )

    def test_auto_scene_matching_uses_user_comment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))

            health = store.get_bundle(scene_id="auto", user_input="膝盖积液疼痛怎么办")
            ai = store.get_bundle(scene_id="auto", user_input="想做AI批量剪辑和自动发布")
            cross = store.get_bundle(scene_id="auto", user_input="跨境电商选品怎么做")

            self.assertEqual(health.scene_id, "health_presales")
            self.assertEqual(ai.scene_id, "ai_hardware_presales")
            self.assertEqual(cross.scene_id, "cross_border_presales")
            self.assertIn("大健康医疗板块", health.materials_context)
            self.assertIn("AI内容生产", ai.materials_context)
            self.assertIn("跨境电商", cross.materials_context)

    def test_bundle_prompt_context_combines_global_scene_and_materials(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            bundle = store.get_bundle(scene_id="health_presales")
            context = bundle.to_prompt_context()

            self.assertIn("<global_operator_prompt>", context)
            self.assertIn("<project_scene_prompt>", context)
            self.assertIn("<project_materials>", context)
            self.assertIn("识别用户是否具备意向", context)
            self.assertIn("强意向", context)
            self.assertIn("暂时不要发送优惠券或企业名片", context)
            self.assertIn("给【XX专属评估入口】", context)
            self.assertIn("关节健康", context)


    def test_rule_document_selection_reads_matching_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            selection = store.select_relevant_documents("膝盖关节疼，想了解骨积液怎么处理", scene_id="health_presales")

            self.assertIn("healthcare_medical", selection["document_ids"])
            selected_doc = next(doc for doc in selection["documents"] if doc["doc_id"] == "healthcare_medical")
            self.assertEqual(selected_doc["sender_identity"], "xx健康顾问助理")
            self.assertIn("sender_identity: xx健康顾问助理", selection["context"])
            self.assertIn("大健康医疗板块", selection["context"])
            self.assertIn("骨积液", selection["context"])

    def test_recruitment_document_selection_reads_recruitment_knowledge_base(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            selection = store.select_relevant_documents(
                "想了解AI项目经理岗位，面试流程和薪资怎么沟通",
                scene_id="auto",
                max_documents=4,
            )

            self.assertIn("recruitment_ai_project_manager", selection["document_ids"])
            self.assertIn("recruitment_interview_process", selection["document_ids"])
            self.assertIn(RECRUITMENT_KNOWLEDGE_BASE_NAME, selection["context"])
            self.assertIn("sender_identity: xx招聘助理", selection["context"])

    def test_deleted_default_documents_are_not_recreated_on_restart(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            project_id = store.default_project_id
            project_dir = Path(temp_dir) / project_id
            ai_manager_dir = (
                project_dir
                / "knowledge"
                / "files"
                / RECRUITMENT_KNOWLEDGE_BASE_NAME
                / "岗位资料"
                / "AI项目经理"
            )
            target_file = ai_manager_dir / "AI项目经理岗位说明.md"
            self.assertTrue(target_file.exists())

            target_file.unlink()
            ai_manager_dir.rmdir()
            restarted = ProjectMaterialStore(Path(temp_dir))
            docs = restarted.list_knowledge_documents(project_id)
            manifest = json.loads((project_dir / "knowledge" / "manifest.json").read_text(encoding="utf-8"))

            self.assertFalse(target_file.exists())
            self.assertFalse(ai_manager_dir.exists())
            self.assertNotIn("recruitment_ai_project_manager", [doc.get("doc_id") for doc in docs])
            self.assertIn("recruitment_ai_project_manager", manifest.get("deleted_doc_ids", []))

    def test_ai_document_selection_reads_selected_file(self):
        class FakeLLM:
            def minimax_anthropic_chat(self, **kwargs):
                return json.dumps({
                    "document_ids": ["ai_technology_hardware"],
                    "reason": "user asks about AI automation",
                })

        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            selection = store.select_relevant_documents(
                "想做AI批量剪辑和自动发布",
                scene_id="ai_hardware_presales",
                llm_tools=FakeLLM(),
                model_conf={
                    "url": "http://fake",
                    "key": "",
                    "func_name": "minimax_anthropic_chat",
                    "model_name": "fake",
                    "max_len_input": 1000,
                },
            )

            self.assertEqual(selection["document_ids"], ["ai_technology_hardware"])
            self.assertIn("AI技术与智能硬件板块", selection["context"])
            self.assertIn("自动发布", selection["context"])

    def test_import_file_to_knowledge_creates_markdown_manifest_and_chunks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            project_id = store.default_project_id

            result = store.import_file_to_knowledge(
                file_name="关节活动资料.txt",
                file_data="骨积液用户可以先做专业评估，再看关节养护方案。".encode("utf-8"),
                project_id=project_id,
            )

            project_dir = Path(temp_dir) / project_id
            self.assertTrue((project_dir / result["relative_path"]).exists())
            self.assertTrue((project_dir / "knowledge" / "chunks.jsonl").exists())
            self.assertIn("imported_", result["doc_id"])

            docs = store.list_knowledge_documents(project_id)
            imported_doc = next(doc for doc in docs if doc["doc_id"] == result["doc_id"])
            self.assertEqual(result["sender_identity"], "xx健康顾问助理")
            self.assertEqual(imported_doc["sender_identity"], "xx健康顾问助理")

            selection = store.select_relevant_documents("骨积液怎么评估", project_id=project_id, scene_id="health_presales")
            self.assertIn(result["doc_id"], selection["document_ids"])
            self.assertIn("专业评估", selection["context"])
            self.assertIn("sender_identity: xx健康顾问助理", selection["context"])

    def test_manifest_restores_documents_from_document_descriptions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            project_id = store.default_project_id
            project_dir = Path(temp_dir) / project_id
            relative_path = "knowledge/files/模拟业务资料/大健康睡眠项目/睡眠初评服务说明.md"
            target = project_dir / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("睡眠状态初步评估，可提供睡眠自测表和资料包。", encoding="utf-8")
            descriptions = {
                "version": 1,
                "project_id": project_id,
                "domains": [
                    {
                        "domain_id": "domain_mock",
                        "name": "模拟业务资料",
                        "sections": [
                            {
                                "section_id": "section_sleep",
                                "name": "大健康睡眠项目",
                                "documents": [
                                    {
                                        "doc_id": "mock_sleep_assessment",
                                        "title": "睡眠初评服务说明",
                                        "relative_path": relative_path,
                                        "description": "当评论涉及睡不好、压力大、睡眠资料或睡眠初评时读取。",
                                        "tags": ["睡眠", "睡不好", "压力大", "资料包", "免费初评"],
                                        "sender_identity": "xx健康顾问助理",
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
            (project_dir / "knowledge" / "document_descriptions.json").write_text(
                json.dumps(descriptions, ensure_ascii=False),
                encoding="utf-8",
            )

            docs = store.list_knowledge_documents(project_id)
            self.assertTrue(any(doc["doc_id"] == "mock_sleep_assessment" for doc in docs))

            selection = store.select_relevant_documents(
                "平时工作压力大，最近老是睡不好",
                project_id=project_id,
                scene_id="auto",
            )
            self.assertIn("mock_sleep_assessment", selection["document_ids"])
            selected_doc = next(doc for doc in selection["documents"] if doc["doc_id"] == "mock_sleep_assessment")
            self.assertEqual(selected_doc["sender_identity"], "xx健康顾问助理")
            self.assertIn("睡眠自测表", selection["context"])


if __name__ == "__main__":
    unittest.main()
