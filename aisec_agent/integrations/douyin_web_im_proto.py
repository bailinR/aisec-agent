"""Small generated-equivalent protobuf schema for the receive path.

The upstream reference repository ships generated ``*_pb2.py`` files. Keeping
the receive schema here avoids importing that repository at runtime while
covering only the PushFrame and new-message fields this adapter needs.
"""

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory


def _field(message: descriptor_pb2.DescriptorProto, name: str, number: int, kind: int, *, type_name: str = "") -> None:
    item = message.field.add()
    item.name = name
    item.number = number
    item.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    item.type = kind
    if type_name:
        item.type_name = type_name


def _build_file_descriptor() -> bytes:
    file_proto = descriptor_pb2.FileDescriptorProto()
    file_proto.name = "douyin_web_im_receive.proto"
    file_proto.package = "aisec_agent.douyin_web_im"
    file_proto.syntax = "proto3"

    headers = file_proto.message_type.add()
    headers.name = "HeadersList"
    _field(headers, "key", 1, descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    _field(headers, "value", 2, descriptor_pb2.FieldDescriptorProto.TYPE_STRING)

    push_frame = file_proto.message_type.add()
    push_frame.name = "PushFrame"
    _field(push_frame, "seqId", 1, descriptor_pb2.FieldDescriptorProto.TYPE_UINT64)
    _field(push_frame, "logId", 2, descriptor_pb2.FieldDescriptorProto.TYPE_UINT64)
    _field(push_frame, "service", 3, descriptor_pb2.FieldDescriptorProto.TYPE_UINT64)
    _field(push_frame, "method", 4, descriptor_pb2.FieldDescriptorProto.TYPE_UINT64)
    headers_field = push_frame.field.add()
    headers_field.name = "headersList"
    headers_field.number = 5
    headers_field.label = descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED
    headers_field.type = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
    headers_field.type_name = ".aisec_agent.douyin_web_im.HeadersList"
    _field(push_frame, "payloadEncoding", 6, descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    _field(push_frame, "payloadType", 7, descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    _field(push_frame, "payload", 8, descriptor_pb2.FieldDescriptorProto.TYPE_BYTES)
    _field(push_frame, "logIdNew", 9, descriptor_pb2.FieldDescriptorProto.TYPE_STRING)

    response = file_proto.message_type.add()
    response.name = "Response"
    _field(response, "cmd", 1, descriptor_pb2.FieldDescriptorProto.TYPE_INT32)
    _field(response, "sequence_id", 2, descriptor_pb2.FieldDescriptorProto.TYPE_INT64)
    _field(response, "error_desc", 3, descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    _field(response, "message", 4, descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    _field(response, "inbox_type", 5, descriptor_pb2.FieldDescriptorProto.TYPE_INT64)
    response_body = file_proto.message_type.add()
    response_body.name = "ResponseBody"
    notification = file_proto.message_type.add()
    notification.name = "NewMessageNotify"
    _field(notification, "conversation_id", 2, descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    _field(notification, "conversation_type", 3, descriptor_pb2.FieldDescriptorProto.TYPE_INT32)
    _field(notification, "notify_type", 4, descriptor_pb2.FieldDescriptorProto.TYPE_INT32)
    _field(notification, "message", 5, descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE, type_name=".aisec_agent.douyin_web_im.MessageBody")
    message_body = file_proto.message_type.add()
    message_body.name = "MessageBody"
    _field(message_body, "conversation_id", 1, descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    _field(message_body, "conversation_type", 2, descriptor_pb2.FieldDescriptorProto.TYPE_INT32)
    _field(message_body, "server_message_id", 3, descriptor_pb2.FieldDescriptorProto.TYPE_INT64)
    _field(message_body, "index_in_conversation", 4, descriptor_pb2.FieldDescriptorProto.TYPE_INT64)
    _field(message_body, "conversation_short_id", 5, descriptor_pb2.FieldDescriptorProto.TYPE_INT64)
    _field(message_body, "message_type", 6, descriptor_pb2.FieldDescriptorProto.TYPE_INT32)
    _field(message_body, "sender", 7, descriptor_pb2.FieldDescriptorProto.TYPE_INT64)
    _field(message_body, "content", 8, descriptor_pb2.FieldDescriptorProto.TYPE_STRING)

    conversation_response = file_proto.message_type.add()
    conversation_response.name = "GetConversationInfoListV2ResponseBody"
    conversation_item = file_proto.message_type.add()
    conversation_item.name = "GetConversationInfoV2Response"
    _field(conversation_item, "conversation_id", 1, descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    _field(conversation_item, "conversation_short_id", 2, descriptor_pb2.FieldDescriptorProto.TYPE_INT64)
    _field(conversation_item, "conversation_type", 3, descriptor_pb2.FieldDescriptorProto.TYPE_INT32)
    _field(conversation_item, "ticket", 4, descriptor_pb2.FieldDescriptorProto.TYPE_STRING)

    def repeated_message(parent: descriptor_pb2.DescriptorProto, name: str, number: int, type_name: str) -> None:
        item = parent.field.add()
        item.name = name
        item.number = number
        item.label = descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED
        item.type = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
        item.type_name = type_name

    repeated_message(conversation_response, "conversation_info_list", 1, ".aisec_agent.douyin_web_im.GetConversationInfoV2Response")

    response_body.oneof_decl.add().name = "body"
    body_field = response_body.field.add()
    body_field.name = "new_message_notify"
    body_field.number = 500
    body_field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    body_field.type = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
    body_field.type_name = ".aisec_agent.douyin_web_im.NewMessageNotify"
    body_field.oneof_index = 0
    response_body_field = response.field.add()
    response_body_field.name = "body"
    response_body_field.number = 6
    response_body_field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    response_body_field.type = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
    response_body_field.type_name = ".aisec_agent.douyin_web_im.ResponseBody"

    return file_proto.SerializeToString()


_pool = descriptor_pool.Default()
try:
    _pool.AddSerializedFile(_build_file_descriptor())
except Exception:
    # The default pool may already contain the schema after a reload.
    pass


def _message_class(name: str):
    descriptor = _pool.FindMessageTypeByName(f"aisec_agent.douyin_web_im.{name}")
    getter = getattr(message_factory, "GetMessageClass", None)
    if getter is not None:
        return getter(descriptor)
    return message_factory.MessageFactory(_pool).GetPrototype(descriptor)


Live_pb2 = type("Live_pb2", (), {"PushFrame": _message_class("PushFrame")})
Response_pb2 = type("Response_pb2", (), {"Response": _message_class("Response")})

