"""Customer support MCP server exposing customer lookup and refund tools."""

import uuid
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field, ValidationError

from src.task1_mcp_server.logging_config import setup_mcp_logging
from src.task1_mcp_server.schemas import (
    CUSTOMER_ID_REGEX,
    CustomerRecordInput,
    CustomerRecordOutput,
    RefundInput,
    RefundOutput,
)

logger = setup_mcp_logging()

# Sample in-memory customer datastore
CUSTOMER_DATABASE: dict[str, dict] = {
    "CUST-A1B2C": {
        "customer_id": "CUST-A1B2C",
        "name": "Jane Doe",
        "status": "active",
        "tier": "enterprise",
        "balance": 1250.00,
    },
    "CUST-98765": {
        "customer_id": "CUST-98765",
        "name": "Acme Corp",
        "status": "active",
        "tier": "standard",
        "balance": 450.00,
    },
}

server = MCPServer(
    name="customer-support-server",
    version="1.0.0",
    description="Customer support tools for record lookup and refund processing",
)


@server.tool(
    name="get_customer_record",
    description="Look up a customer record by ID (format: CUST-XXXXX).",
)
async def get_customer_record(
    customer_id: Annotated[
        str,
        Field(
            pattern=CUSTOMER_ID_REGEX,
            description="Customer identifier formatted as CUST-XXXXX",
        ),
    ],
) -> str:
    logger.info("Lookup requested for customer_id=%s", customer_id)

    try:
        validated = CustomerRecordInput(customer_id=customer_id)
    except ValidationError as e:
        logger.error("Validation error for customer_id: %s", e)
        raise ToolError(f"Invalid input: {e.errors()}") from e

    cid = validated.customer_id
    if cid in CUSTOMER_DATABASE:
        return CustomerRecordOutput(**CUSTOMER_DATABASE[cid]).model_dump_json()

    # Fallback response if not in mock store
    return CustomerRecordOutput(
        customer_id=cid,
        name=f"Customer {cid}",
        status="active",
        tier="standard",
        balance=0.00,
    ).model_dump_json()


@server.tool(
    name="trigger_refund",
    description="Process a customer refund (positive amount, reason >= 10 chars).",
)
async def trigger_refund(
    customer_id: Annotated[
        str,
        Field(
            pattern=CUSTOMER_ID_REGEX,
            description="Customer identifier formatted as CUST-XXXXX",
        ),
    ],
    amount: Annotated[
        float,
        Field(
            gt=0.0,
            description="Positive refund amount",
        ),
    ],
    reason: Annotated[
        str,
        Field(
            min_length=10,
            description="Reason for refund",
        ),
    ],
) -> str:
    logger.info("Refund requested: customer_id=%s, amount=%.2f", customer_id, amount)

    try:
        validated = RefundInput(customer_id=customer_id, amount=amount, reason=reason)
    except ValidationError as e:
        logger.error("Validation error in trigger_refund: %s", e)
        raise ToolError(f"Invalid input: {e.errors()}") from e

    refund_id = f"ref_{uuid.uuid4().hex[:8]}"
    output = RefundOutput(
        refund_id=refund_id,
        customer_id=validated.customer_id,
        amount=validated.amount,
        reason=validated.reason,
        status="processed",
    )
    logger.info("Processed refund %s", refund_id)
    return output.model_dump_json()


def main() -> None:
    logger.info("Starting MCP Server on stdio transport...")
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
