from datetime import datetime
from sqlalchemy import (
    JSON,
    String,
    ForeignKey,
    Integer,
    Text,
    BigInteger,
    DateTime,
    Column,CHAR, SmallInteger,
)
from aisec_agent.model.define import Base


class PromptModel(Base):
    __tablename__ = "prompt"

    id = Column(Integer, primary_key=True, autoincrement=True, comment='表id')
    prompt_id = Column(String(255), nullable=False, comment='prompt_id')
    file_name = Column(String(255), nullable=False, comment='prompt文件名')
    description = Column(String(255), nullable=True, comment='描述')
    model_name = Column(String(255), nullable=True, comment='使用的模型名称')
    num_ctx = Column(String(255), nullable=True, comment='最长输入')
    num_predict = Column(String(255), nullable=True, comment='最长输出')
    created_at = Column(DateTime, nullable=True, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, nullable=True, default=datetime.now, onupdate=datetime.now, comment='更新时间')
    is_enable = Column(Integer, nullable=False, default=1, comment='是否启用')
    is_delete = Column(Integer, nullable=False, default=0, comment='是否删除')
    type = Column(Integer, nullable=False, default=0, comment='prompt的类型：通用-1；特殊-2')


class LLMConfigModel(Base):
    __tablename__ = "llm_config"

    id = Column(Integer, primary_key=True, autoincrement=True, comment='表id')
    url = Column(String(255), nullable=False, comment='大模型调用的url')
    key = Column(String(255), nullable=True, comment='api key')
    function_name = Column(String(255), nullable=False, comment='API源')
    model_name = Column(String(255), nullable=False, comment='使用的模型名称')
    type = Column(String(255), nullable=True, comment='模型源类型')
    created_at = Column(DateTime, nullable=True, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, nullable=True, default=datetime.now, onupdate=datetime.now, comment='更新时间')
    max_len_input = Column(Integer, nullable=False, comment='最大输入长度')
    model_type = Column(String(255), nullable=False, comment='模型类型')
    user_id = Column(BigInteger, nullable=False, comment="用户id")
    is_public = Column(Integer, nullable=False, comment="是否公开")


class DefaultLLMConfigModel(Base):
    __tablename__ = "default_llm_config"

    id = Column(Integer, primary_key=True, autoincrement=True, comment='表id')
    user_id = Column(BigInteger, nullable=False, comment="用户id")
    default_conf = Column(JSON, nullable=True, comment="系统模型默认配置")


class AssetsModel(Base):
    __tablename__ = "ai_assets"

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment='资产id')
    assets_name = Column(String(128), nullable=True, comment='资产名称')
    assets_type = Column(String(32), nullable=True, comment='资产类型')
    assets_ip = Column(String(256), nullable=True, comment='地址:主机IP或网站访问地址')
    status = Column(String(20), nullable=True, comment='状态:正常/停用')
    update_time = Column(DateTime, nullable=True, comment='更新时间')
    create_time = Column(DateTime, nullable=True, comment='创建时间')
    sync_id = Column(String(128), nullable=True, comment='资产管理系统中的同步ID')
    sync_status = Column(SmallInteger, nullable=True, comment='同步状态:0未同步，1同步中，2同步完成')
    sync_time = Column(DateTime, nullable=True, comment='同步时间')
    online = Column(String(20), nullable=True, comment='在线状态:离线/在线/未知,无法查询是否在线')
    assets_os = Column(String(100), nullable=True, comment='操作系统')
    detect_status = Column(SmallInteger, default=0, comment='检测状态:0待更新，1已更新')
    remark = Column(String(255), nullable=True, comment='备注')
    affiliation = Column(String(255), nullable=True, comment='所属单位')
    dept_name = Column(String(256), default='', comment='维护负责部门')
    aegis_dept_principal = Column(String(256), default='', comment='资产维护人')
    principal_phone = Column(String(256), default='', comment='维护人联系电话')
    assets_level = Column(String(10), nullable=True, comment='资产重要程度')
    area = Column(String(20), nullable=True, comment='物理位置')


class AssetsManageModel(Base):
    __tablename__ = "ai_assets_manage"
    __table_args__ = ({'comment': '管理信息'})  # 对应 SQL 中的 COMMENT '管理信息'

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment='记录id')
    assets_id = Column(BigInteger, ForeignKey('ai_assets.id'), nullable=True, comment='关联的资产ID')
    vendor = Column(String(256), nullable=True, default='', comment='供应商, 即供应该资产的公司名称, 通常需要模糊匹配并过滤空数据')
    vendor_principal = Column(String(256), nullable=True, default='', comment='供应商的负责人')
    vendor_principal_phone = Column(String(256), nullable=True, default='', comment='开发人联系电话')


class VulnModel(Base):
    __tablename__ = "ai_vuln"

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment='记录id')
    host = Column(String(255), nullable=True, comment='主机地址')
    vuln_name = Column(String(255), nullable=True, comment='漏洞名称')
    vuln_type_name = Column(String(255), nullable=True, comment='漏洞类型')
    vuln_level = Column(String(10), nullable=True, comment='漏洞等级: 低危/中危/高危')
    assets_id = Column(BigInteger, ForeignKey('ai_assets.id'), nullable=True, comment='关联的资产ID')
    vuln_count = Column(Integer, nullable=True, default=0, comment='漏洞数量')
    dept_name = Column(String(255), nullable=True, comment='部门名称')
    vuln_status = Column(String(50), nullable=True, comment='漏洞状态: 待修复/已修复')
    issued_no = Column(String(50), nullable=True, comment='下发编号')
    issued_status = Column(String(10), nullable=True, comment='下发状态')
    create_time = Column(DateTime, nullable=True, comment='创建时间: 漏洞产生时间')
    sync_id = Column(String(128), nullable=True, comment='漏洞管理平台上记录的id')
    sync_status = Column(SmallInteger, nullable=True, comment='同步的状态')
    sync_time = Column(DateTime, nullable=True, comment='同步的时间')
    vuln_desc = Column(Text, nullable=True, comment='漏洞描述')
    cause = Column(Text, nullable=True, comment='产生原因')
    repair_suggest = Column(Text, nullable=True, comment='解决建议')


class AIReportTemplateModel(Base):
    __tablename__ = "ai_report_template"

    id = Column(Integer, primary_key=True, autoincrement=True, comment='表id')
    template_id = Column(Integer, nullable=False, comment='模板id，采用uuid')
    template_name = Column(String(255), nullable=False, comment='模板名称')
    file_path = Column(String(255), nullable=False, comment='minio上存储的文件路径, 【私密信息,不可查询】')
    key_json = Column(String(2000), nullable=False,
                      comment='关键词信息：[{name:关键词名称,key:关键词键名,description:描述}]')
    self_key_json = Column(String(255), nullable=True,
                           comment='java自用key_json，以便处理后续可能出现的大模型无法处理好的关键词，采用硬规则处理')
    description = Column(String(255), nullable=False,
                         comment='模板的描述：用于资产统计漏洞的模板/用于自定义漏洞统计的模板')
    type_id = Column(SmallInteger, nullable=False,
                     comment='模板类型：采用枚举值，java和python都采用共同的枚举值，后续可能要搞个枚举表')
    is_enable = Column(SmallInteger, nullable=False, default=1, comment='是否启用：0否/1是 默认值：1')
    is_delete = Column(SmallInteger, nullable=False, default=0, comment='是否删除：0未删除/1已删除 默认值：0')
    remark = Column(String(255), nullable=True, comment='备注，预留')
    create_time = Column(DateTime, nullable=False, default=datetime.now, comment='创建时间')
    update_time = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now, comment='更新时间')
    create_by = Column(String(50), nullable=True, default='', comment='创建者')
    update_by = Column(String(50), nullable=True, default='', comment='修改者')


class TimeMixin:
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    deleted_at = Column(DateTime, index=True, nullable=True)


class DbSettingsModel( Base, TimeMixin):
    __tablename__ = 'db_settings'
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(CHAR(length=100), comment="数据库名词")
    host = Column(CHAR(length=100), comment="ip 地址")
    port = Column(Integer)
    user = Column(CHAR(length=100), comment="用户名")
    pwd = Column(CHAR(length=100), comment="密码")
    db_typ = Column(CHAR(length=100), comment="数据库类型")
    db_name = Column(CHAR(length=100), comment="数据库名")
    schema = Column(CHAR(length=100), comment="在使用cache db2 客户中需要存储该字段")
    db_config = Column(JSON, comment="数据库配置")
    sc_config = Column(JSON, comment="扫表配置")
    enable = Column(Integer, comment="是否可以用")


class TablesDesModel(Base, TimeMixin):
    __tablename__ = 'table_detail'
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    db_id = Column(Integer, comment="数据库id")
    table_name = Column(CHAR(length=100), comment="表英文名")
    name = Column(CHAR(length=255), comment="数据库中字段中文")
    ch_name = Column(CHAR(length=255), comment="人工标注 表中文名")
    ai_name = Column(CHAR(length=255), comment="ai 备注名")
    last_update = Column(DateTime, comment="最后更新时间")


class DesTablesModel(Base, TimeMixin):
    __tablename__ = 'detail_table'
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    db_id = Column(Integer, comment="数据库id")
    table_name = Column(CHAR(length=100), comment="表英文名")
    name = Column(CHAR(length=255), comment="数据库中字段中文")
    ch_name = Column(CHAR(length=255), comment="人工标注 表中文名")
    ai_name = Column(CHAR(length=255), comment="ai 备注名")
    last_update = Column(DateTime, comment="最后更新时间")


class ColumnDesModel(Base, TimeMixin):
    __tablename__ = 'detail_column'
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    db_id = Column(Integer, comment="数据库id")
    tb_id = Column(Integer, comment="table id")
    column_name = Column(CHAR(length=100), comment="字段英文名")
    column_type = Column(String(50), comment="字段类型")
    name = Column(CHAR(length=255), comment="数据库中字段中文")
    ch_name = Column(CHAR(length=255), comment="字段中文名")
    ai_name = Column(CHAR(length=255), comment="ai 备注名")
    scanner_result = Column(JSON, comment="扫描结果")
    sample_data = Column(Text, comment="样本数据")

    def to_dict(self):
        return {
            "id": self.id,
            "db_id": self.db_id,
            "tb_id": self.tb_id,
            "column_name": self.column_name,
            "column_type": self.column_type,
            "name": self.name,
            "ai_name": self.ai_name,
            "scanner_result": self.scanner_result,
            "sample_data": self.sample_data,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "deleted_at": self.deleted_at
        }


class QueryTaskModel(Base, TimeMixin):
    __tablename__ = 'query_task_model'
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    dt = Column(Integer, comment="日期格式 20210613")
    task_id = Column(CHAR(length=100), comment="任务名称")
    sql = Column(CHAR(length=100), comment="sql语句")
    status = Column(Integer, default=0, comment="状态 0 未执行 1 执行中 2 执行完成 3 执行失败")
    result = Column(JSON, comment="结果")
    extras = Column(JSON, default={}, comment="其他")


class AIKnowledgeModel(Base, TimeMixin):
    __tablename__ = 'ai_knowledge'

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment='id')
    knowledge_name = Column(String(255), nullable=False, comment='知识库名称')
    language = Column(String(32), comment='语言')
    knowledge_type = Column(Integer, comment='知识库类型')
    description = Column(String(512), comment='说明')
    status = Column(Integer, nullable=False, comment='状态，0.未开始，1.训练中，2.已完成，3.失败')
    finish_time = Column(DateTime, comment='完成时间')
    create_time = Column(DateTime, comment='创建时间')
    update_time = Column(DateTime, comment='修改时间')
    create_by = Column(String(50), server_default='', comment='创建者')
    update_by = Column(String(50), server_default='', comment='修改者')


class DBSQLModel(Base):
    __tablename__ = "db_sql"

    id = Column(Integer, primary_key=True, autoincrement=True, comment='记录id')
    db_id = Column(Integer, nullable=True, comment='数据库id')
    name = Column(String(255), nullable=True, comment='名称')
    search = Column(JSON, nullable=True, comment='sql或请求体')
    remark = Column(String(500), nullable=True, comment='备注')
    create_time = Column(DateTime, nullable=True, comment='创建时间')
    update_time = Column(DateTime, nullable=True, comment='更新时间')
    create_by = Column(String(50), nullable=True, default='', comment='创建者')
    update_by = Column(String(50), nullable=True, default='', comment='修改者')


class AIDataCenterModel(Base):
    __tablename__ = "ai_data_center"

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment='id')
    name = Column(String(255), nullable=False, comment='名称')
    description = Column(String(512), nullable=True, comment='说明')
    db_id = Column(Integer, nullable=True, comment='数据基座')
    create_time = Column(DateTime, nullable=True, comment='创建时间')
    update_time = Column(DateTime, nullable=True, comment='修改时间')
    create_by = Column(String(50), nullable=True, default='', comment='创建者')
    update_by = Column(String(50), nullable=True, default='', comment='修改者')