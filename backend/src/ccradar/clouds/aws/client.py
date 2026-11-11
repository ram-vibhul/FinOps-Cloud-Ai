"""Talking to customer AWS accounts through their read-only role.

``AwsGateway`` is the only place that touches boto3. Tests inject a fake with the
same methods, so no test ever calls AWS.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol

ROLE_ARN = re.compile(r"^arn:aws(-cn|-us-gov)?:iam::(\d{12}):role/[\w+=,.@/-]{1,512}$")


class AwsAccessError(Exception):
    """The role could not be used. ``code`` is safe to show to the customer."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def account_from_arn(role_arn: str) -> str:
    match = ROLE_ARN.match(role_arn)
    if not match:
        raise AwsAccessError(
            "invalid_role_arn", "That is not an IAM role ARN (arn:aws:iam::123456789012:role/Name)."
        )
    return match.group(2)


@dataclass(frozen=True)
class Identity:
    account_id: str
    arn: str


class AwsGateway(Protocol):
    def assume(self, role_arn: str, external_id: str, session_name: str) -> Any:
        """Return a boto3 ``Session`` (or a fake) acting as the customer's role."""
        ...

    def identity(self, session: Any) -> Identity: ...

    def organization_master(self, session: Any) -> str | None:
        """Management account id if the account belongs to an organization."""
        ...

    def client(self, session: Any, service: str, region: str | None = None) -> Any:
        """A boto3 client (or fake) for ``service`` acting as the customer's role."""
        ...


class Boto3Gateway:
    """Real implementation. Credentials for *our* principal come from the default
    chain (instance role, ECS task role, environment)."""

    def __init__(self, region: str = "us-east-1") -> None:
        self.region = region
        self._cache: dict[tuple[str, str], tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def assume(self, role_arn: str, external_id: str, session_name: str) -> Any:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError

        key = (role_arn, external_id)
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached[0] - time.time() > 300:
                return cached[1]
        try:
            sts = boto3.client("sts", region_name=self.region)
            creds = sts.assume_role(
                RoleArn=role_arn,
                ExternalId=external_id,
                RoleSessionName=session_name[:64],
                DurationSeconds=3600,
            )["Credentials"]
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            raise AwsAccessError(
                "access_denied" if code == "AccessDenied" else "aws_error",
                "AWS refused to let CloudCostRadar assume the role. Check the role "
                "ARN, the external ID and that the stack finished.",
            ) from exc
        except BotoCoreError as exc:
            raise AwsAccessError("aws_unreachable", "Could not reach AWS.") from exc
        session = boto3.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
            region_name=self.region,
        )
        with self._lock:
            self._cache[key] = (creds["Expiration"].timestamp(), session)
        return session

    def identity(self, session: Any) -> Identity:
        data = session.client("sts").get_caller_identity()
        return Identity(account_id=data["Account"], arn=data["Arn"])

    def organization_master(self, session: Any) -> str | None:
        from botocore.exceptions import ClientError

        try:
            org = session.client("organizations").describe_organization()["Organization"]
        except ClientError:
            return None
        return org.get("MasterAccountId")

    def client(self, session: Any, service: str, region: str | None = None) -> Any:
        from botocore.config import Config

        config = Config(
            retries={"max_attempts": 8, "mode": "adaptive"}, user_agent_extra="CloudCostRadar"
        )
        return session.client(service, region_name=region or self.region, config=config)
