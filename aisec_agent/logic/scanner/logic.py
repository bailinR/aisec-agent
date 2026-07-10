from ._base import  DatabaseSchemaExplorer
from ...model.schema import MessageTyp, EventTyp, DBScannerEvent, DBScannerRetModel,ColumnRetModel
from .red import  DBScannerQueue
import logging
from ...tools import  is_select_only
from ...model.define import session_ctx, BaseHandler
from typing import Optional,AnyStr
import uuid
from typing import List,Dict
from sqlalchemy import func,case
from ...model.model import DbSettingsModel, DesTablesModel,ColumnDesModel
class ScannerHandler(DatabaseSchemaExplorer):

    def scanner_db(self,  task_id,name:str, message_typ=MessageTyp.MSSQL,
        event_typ=EventTyp.CREATE,
        db_name='db name',
        host='127.0.0.1',port=None,
        user='username',
        password='password',
        table_name = 'information_schema',):
        """创建一个扫描表的任务"""
        logging.info(f"开始创建一个扫描表任务,task_id:{task_id}:{db_name}")
        DBScannerQueue().append( DBScannerEvent(
        task_id=task_id,name=name,
        message_typ=message_typ,
        event_typ= event_typ,
        db_name=db_name,
        host=host,port=port,
        user= user,
        password=password,
        table_name =table_name,
        ) )
        return task_id


    def exec_sql(self, sql: str,host:AnyStr,db_name:AnyStr,user:AnyStr,port:Optional[int],message_typ:MessageTyp,table_name:str="",mapping=[]):
        if not is_select_only:
            raise f"仅限查询语句：「{sql}」"
        with session_ctx() as session:
            db_settings = self.get_db_settings(session,host,db_name,user,port,message_typ)
            if not db_settings:
                raise f"数据库不存在：「{db_name}」"
            pwd = self.get_password(session,host,db_name,user,port,message_typ)
            task_id = uuid.uuid4().hex
            DBScannerQueue().append(DBScannerEvent(
                task_id=task_id,name=db_settings.name,
                message_typ=message_typ,
                event_typ=EventTyp.QUERYSQL,
                db_name=db_name,
                host=host, port=port,
                user=user,
                password= pwd,
                table_name=table_name,
                sql=sql,mapping=mapping
            ))

            return task_id



    def get_table_detail(self, db_title: str, )-> list[DBScannerRetModel]:
        with session_ctx() as session:
            conditions = [
                DbSettingsModel.name.like(f"%{db_title}%")
            ]
            res = (
                session.query( DesTablesModel, DbSettingsModel)
                .join(DbSettingsModel,  DesTablesModel.db_id == DbSettingsModel.id)
                .filter(*conditions)
                .all()
            )

            data = []
            for tb,db in res:
                data.append(DBScannerRetModel(
                    db_name=db.name,host=db.host,
                    user=db.user, port=db.port,
                    table_name=tb.table_name,
                    ch_name=tb.ch_name,
                    columns=[ColumnRetModel(**t ) for t in tb.columns ]if tb.columns else None,
                    last_update=tb.last_update,
                ))
            return data


class ScannerBaseHandler(BaseHandler):
    def get_table_list(self, db_ids: list[int]) -> list[dict[str, str]]:
        with session_ctx() as session:
            tables = (
                session.query(  DesTablesModel.db_id, DesTablesModel.table_name,  DesTablesModel.ai_name,  DesTablesModel.ch_name , DesTablesModel.name)
                .filter( DesTablesModel.db_id.in_(db_ids))
                .all()
            )
            result = {}
            for t in tables:
                if t.db_id not in result:
                    result[t.db_id] = []

                result[t.db_id].append({
                    "table_name": t.table_name,
                    "ch_name": t.ch_name or t.ai_name or t.name or "无中文备注"
                })

            return result

    def get_db_and_tables_markdown(self, table_names: list[str], db_ids: list[int]):
        with session_ctx() as db:
            # 1. 查数据源
            dbs = db.query(DbSettingsModel).filter(DbSettingsModel.id.in_(db_ids)).all()
            db_map = {x.id: x for x in dbs}

            # 2. 查所有表
            tables = db.query( DesTablesModel).filter(
                 DesTablesModel.db_id.in_(db_ids),
                 DesTablesModel.table_name.in_(table_names)
            ).all()
            table_map = {}
            for t in tables:
                table_map.setdefault(t.db_id, []).append(t)

            # 3. 查所有字段
            tb_ids = [t.id for t in tables]
            columns = db.query(ColumnDesModel).filter(ColumnDesModel.tb_id.in_(tb_ids)).all()
            col_map = {}
            for col in columns:
                col_map.setdefault(col.tb_id, []).append(col)

            # 4. 生成 Markdown
            result = []
            for db_id, dbitem in db_map.items():
                # 构建表格信息
                tables_info = []
                tables = table_map.get(db_id, [])

                for tb in tables:
                    zh_name = tb.ai_name or tb.ch_name or tb.name or "无中文备注"

                    # 构建字段的markdown表格
                    table_md = f"#### 表名: {tb.table_name}\n表描述: {zh_name}\n\n"
                    table_md += "| 字段名 | 字段类型 | 字段描述 |\n|--------|----------|----------|\n"

                    for col in col_map.get(tb.id, []):
                        col_zh = col.ai_name or col.ch_name or col.name or "无中文备注"
                        table_md += f"| {col.column_name} | {col.column_type} | {col_zh} |\n"

                    tables_info.append(table_md)

                # 构建数据库配置对象
                db_result = {
                    "db_config": dbitem.sc_config,
                    "tables": "\n\n".join(tables_info)
                }

                result.append(db_result)

            return result

    def get_table_detail(self, db_title: str, ) -> list[DBScannerRetModel]:
        pass

    def exec_sql(self, sql: str, host: AnyStr, db_name: AnyStr, user: AnyStr, port: Optional[int],
                 message_typ: MessageTyp, table_name: str = "") -> List[Dict]:
        """执行 SQL 查询语句，仅允许 SELECT 查询"""
        pass

