from email.message import EmailMessage
from email.utils import formataddr

import aiosmtplib

VERIFY_TIMEOUT = 10
SEND_TIMEOUT = 30


class AiosmtplibMailer:
    async def verify_credentials(
        self, host: str, port: int, email: str, password: str
    ) -> bool:
        client = aiosmtplib.SMTP(
            hostname=host, port=port, use_tls=port == 465, timeout=VERIFY_TIMEOUT
        )
        try:
            await client.connect()
            await client.login(email, password)
            return True
        except Exception:
            return False
        finally:
            try:
                await client.quit()
            except Exception:
                pass

    async def send(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        from_name: str | None,
        from_email: str,
        to_email: str,
        subject: str,
        text_body: str,
        html_body: str,
        attachments: list[tuple[str, bytes, str]],
    ) -> None:
        message = EmailMessage()
        message["From"] = formataddr((from_name, from_email)) if from_name else from_email
        message["To"] = to_email
        message["Subject"] = subject
        message.set_content(text_body)
        message.add_alternative(html_body, subtype="html")
        for filename, content, mime_type in attachments:
            maintype, _, subtype = mime_type.partition("/")
            message.add_attachment(
                content, maintype=maintype or "application", subtype=subtype or "octet-stream",
                filename=filename,
            )
        await aiosmtplib.send(
            message,
            hostname=host,
            port=port,
            username=username,
            password=password,
            use_tls=port == 465,
            timeout=SEND_TIMEOUT,
        )
