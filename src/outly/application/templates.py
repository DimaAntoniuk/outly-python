import uuid

from ..domain.entities import EmailTemplate
from .errors import BadRequest, Conflict, Forbidden, NotFound
from .ports import TemplateRepository
from .throttling import utc_now


class TemplateService:
    def __init__(self, template_repo: TemplateRepository):
        self._template_repo = template_repo

    async def create(self, user_id: str, name: object, subject: object, body: object) -> EmailTemplate:
        if not isinstance(name, str) or not name.strip():
            raise BadRequest("Template name is required")
        if not isinstance(subject, str) or not subject:
            raise BadRequest("Missing required fields: subject")
        if not isinstance(body, str) or not body:
            raise BadRequest("Missing required fields: body")
        if await self._template_repo.name_exists(user_id, name.strip()):
            raise Conflict("A template with this name already exists")
        now = utc_now()
        template = EmailTemplate(
            id=uuid.uuid4().hex,
            user_id=user_id,
            name=name.strip(),
            subject=subject,
            body=body,
            created_at=now,
            updated_at=now,
        )
        return await self._template_repo.create(template)

    async def list_for_user(self, user_id: str) -> list[EmailTemplate]:
        return await self._template_repo.list_for_user(user_id)

    async def _get_owned(self, user_id: str, template_id: str) -> EmailTemplate:
        template = await self._template_repo.get(template_id)
        if template is None:
            raise NotFound("Template not found")
        if template.user_id != user_id:
            raise Forbidden("Forbidden")
        return template

    async def update(
        self,
        user_id: str,
        template_id: str,
        name: object = None,
        subject: object = None,
        body: object = None,
    ) -> EmailTemplate:
        template = await self._get_owned(user_id, template_id)
        if name is not None:
            if not isinstance(name, str) or not name.strip():
                raise BadRequest("Template name is required")
            if await self._template_repo.name_exists(user_id, name.strip(), exclude_id=template_id):
                raise Conflict("A template with this name already exists")
            template.name = name.strip()
        if subject is not None and isinstance(subject, str):
            template.subject = subject
        if body is not None and isinstance(body, str):
            template.body = body
        template.updated_at = utc_now()
        return await self._template_repo.update(template)

    async def delete(self, user_id: str, template_id: str) -> None:
        await self._get_owned(user_id, template_id)
        await self._template_repo.delete(template_id)
