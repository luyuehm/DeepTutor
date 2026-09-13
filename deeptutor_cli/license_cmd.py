"""CLI commands for the DeepTutor Enterprise license layer.

Usage (once auth is enabled / license layer bootstrapped)::

    deeptutor license self-check
    deeptutor license customer add --id acme --name "Acme Inc"
    deeptutor license issue --customer acme --granularity seats --seats 5
    deeptutor license verify --token DTLIC-...
    deeptutor license revoke --license-id LIC-...
    deeptutor license usage --license-id LIC-...

No secret is ever echoed: the issuer key is read from the environment or the
stable local key file, and the ``issue`` output prints the token to stdout
only because that is the deliverable an operator pastes into a customer
deployment.
"""

from __future__ import annotations

from datetime import datetime, timezone

import typer


def register(app: typer.Typer) -> None:
    @app.command("self-check")
    def self_check() -> None:
        """Sign and verify a throwaway license for this deployment."""
        from deeptutor.license.service import self_check as _self_check

        payload = _self_check()
        typer.echo(f"OK tenant={payload.get('tenant_id')}")

    @app.command("customer")
    def customer(
        add: str = typer.Option("", "--add", help="Customer id to add"),
        name: str = typer.Option("", "--name", help="Display name"),
        contact: str = typer.Option("", "--contact", help="Contact info"),
        notes: str = typer.Option("", "--notes", help="Free-form notes"),
    ) -> None:
        """Add a customer (tenant) to the license registry."""
        from deeptutor.license.service import list_customers, save_customer

        if add:
            save_customer(
                {
                    "id": add,
                    "name": name or add,
                    "contact": contact,
                    "notes": notes,
                    "status": "active",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            typer.echo(f"Customer {add} added.")
            return
        for record in list_customers():
            typer.echo(f"{record.get('id')}\t{record.get('name')}\t{record.get('status')}")

    @app.command("issue")
    def issue(
        customer: str = typer.Option(..., "--customer", "-c", help="Customer id"),
        tenant: str = typer.Option("", "--tenant", help="Tenant id (default: customer)"),
        granularity: str = typer.Option(
            "org", "--granularity", help="org|seats|concurrency"
        ),
        seats: int = typer.Option(0, "--seats", help="Seat count (seats granularity)"),
        concurrency: int = typer.Option(
            0, "--concurrency", help="Max concurrency (concurrency granularity)"
        ),
        features: str = typer.Option("", "--features", help="Comma-separated feature flags"),
        days: int = typer.Option(365, "--days", help="Validity in days"),
    ) -> None:
        """Issue a signed license token for a customer."""
        from deeptutor.license.service import issue_license

        feature_list = [f.strip() for f in features.split(",") if f.strip()]
        try:
            issued = issue_license(
                customer_id=customer,
                tenant_id=tenant,
                granularity=granularity,
                seats=seats,
                max_concurrency=concurrency,
                features=feature_list,
                expires_in_days=days,
            )
        except Exception as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(f"License {issued['license_id']} issued for tenant {issued['tenant_id']}")
        typer.echo(issued["token"])

    @app.command("verify")
    def verify(
        token: str = typer.Option(..., "--token", help="License token"),
        tenant: str = typer.Option("", "--tenant", help="Require this tenant"),
        features: str = typer.Option("", "--features", help="Required comma-separated features"),
    ) -> None:
        """Verify a license token."""
        from deeptutor.license.service import verify_license

        required = [f.strip() for f in features.split(",") if f.strip()]
        try:
            verified = verify_license(token, tenant_id=tenant or None, features_required=required)
        except Exception as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(
            f"OK license={verified.claims.license_id} tenant={verified.claims.tenant_id} "
            f"granularity={verified.claims.granularity} expires={verified.claims.expires_at}"
        )

    @app.command("revoke")
    def revoke(
        license_id: str = typer.Option(..., "--license-id", help="License id to revoke"),
        unrevoke: bool = typer.Option(False, "--unrevoke", help="Restore a revoked license"),
    ) -> None:
        """Revoke (or restore) a license by id."""
        from deeptutor.license.service import revoke_license, unrevoke_license

        if unrevoke:
            unrevoke_license(license_id)
            typer.echo(f"License {license_id} restored.")
        else:
            revoke_license(license_id)
            typer.echo(f"License {license_id} revoked.")

    @app.command("usage")
    def usage(license_id: str = typer.Option(..., "--license-id", help="License id")) -> None:
        """Show seat/concurrency usage for a license."""
        from deeptutor.license.service import license_usage

        payload = license_usage(license_id)
        typer.echo(
            f"seats {payload['seats']}/{payload['seats_limit']}  "
            f"sessions {payload['active_sessions']}/{payload['concurrency_limit']}"
        )


__all__ = ["register"]
