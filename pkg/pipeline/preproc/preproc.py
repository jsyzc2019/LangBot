from __future__ import annotations

import datetime

from .. import stage, entities, stagemgr
from ...core import entities as core_entities
from ...provider import entities as llm_entities
from ...plugin import events
from ...platform.types import message as platform_message


@stage.stage_class("PreProcessor")
class PreProcessor(stage.PipelineStage):
    """请求预处理阶段

    签出会话、prompt、上文、模型、内容函数。

    改写：
        - session
        - prompt
        - messages
        - user_message
        - use_model
        - use_funcs
    """

    async def process(
        self,
        query: core_entities.Query,
        stage_inst_name: str,
    ) -> entities.StageProcessResult:
        """处理
        """
        session = await self.ap.sess_mgr.get_session(query)

        conversation = await self.ap.sess_mgr.get_conversation(session)

        # 设置query
        query.session = session
        query.prompt = conversation.prompt.copy()
        query.messages = conversation.messages.copy()

        query.use_model = conversation.use_model

        query.use_funcs = conversation.use_funcs if query.use_model.tool_call_supported else None

        query.variables = {
            "session_id": f"{query.session.launcher_type.value}_{query.session.launcher_id}",
            "conversation_id": conversation.uuid,
            "msg_create_time": int(query.message_event.time) if query.message_event.time else int(datetime.datetime.now().timestamp()),
        }

        # 检查vision是否启用，没启用就删除所有图片
        if not self.ap.provider_cfg.data['enable-vision'] or (self.ap.provider_cfg.data['runner'] == 'local-agent' and not query.use_model.vision_supported):
            for msg in query.messages:
                if isinstance(msg.content, list):
                    for me in msg.content:
                        if me.type == 'image_url':
                            msg.content.remove(me)

        content_list = []

        plain_text = ""

        for me in query.message_chain:
            if isinstance(me, platform_message.Plain):
                content_list.append(
                    llm_entities.ContentElement.from_text(me.text)
                )
                plain_text += me.text
            elif isinstance(me, platform_message.Image):
                if self.ap.provider_cfg.data['enable-vision'] and (self.ap.provider_cfg.data['runner'] != 'local-agent' or query.use_model.vision_supported):
                    if me.base64 is not None:
                        content_list.append(
                            llm_entities.ContentElement.from_image_base64(me.base64)
                        )

        query.variables['user_message_text'] = plain_text

        query.user_message = llm_entities.Message(
            role='user',
            content=content_list
        )
        # =========== 触发事件 PromptPreProcessing

        event_ctx = await self.ap.plugin_mgr.emit_event(
            event=events.PromptPreProcessing(
                session_name=f'{query.session.launcher_type.value}_{query.session.launcher_id}',
                default_prompt=query.prompt.messages,
                prompt=query.messages,
                query=query
            )
        )

        query.prompt.messages = event_ctx.event.default_prompt
        query.messages = event_ctx.event.prompt

        return entities.StageProcessResult(
            result_type=entities.ResultType.CONTINUE,
            new_query=query
        )
