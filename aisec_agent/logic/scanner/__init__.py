
from abc import ABC, abstractmethod

from typing import List, Dict

from ...model.schema import DBScannerRetModel,MessageTyp
from ...model.model import TablesDesModel,QueryTaskModel,DbSettingsModel
from ...tools.ase import PasswordCrypto
from ...config import AK,SK
from typing import  Optional,AnyStr

class  DatabaseSchemaExplorer(ABC):




    @abstractmethod
    def get_table_detail(self, db_title: str, ) -> list[DBScannerRetModel]:
        """获取指定表的详细结构"""
        pass

    @abstractmethod
    def exec_sql(self, sql: str,host:AnyStr,db_name:AnyStr,user:AnyStr,port:Optional[int],message_typ:MessageTyp,table_name:str="")-> List[Dict]:
        """执行 SQL 查询语句，仅允许 SELECT 查询"""
        pass


    def get_db_settings(self,ctx,host,db_name,user,port,typ)  :
        """获取真实数据库密码"""
        conditions =[
            DbSettingsModel.host ==  host,
            DbSettingsModel.port ==  port,
            DbSettingsModel.db_name ==  db_name,
            DbSettingsModel.user == user,
            DbSettingsModel.db_typ == MessageTyp.descriptions()[typ],
        ]
        return ctx.query(DbSettingsModel).filter(*conditions).first()


    def get_password(self,ctx,host,db_name,user,port,typ) -> str:
        """获取真实数据库密码"""
        db_settings = self.get_db_settings(ctx,host,db_name,user,port,typ)
        if not db_settings:
            raise "数据库配置不存在"
        return   PasswordCrypto(AK,SK,db_settings.created_at).decrypt(db_settings.pwd)
    

