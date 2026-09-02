# =============================================================================
# Stratum AI - Knowledge Graph Cypher Queries (Apache AGE)
# =============================================================================
"""
Cypher query construction for the Apache AGE knowledge graph.

Provides:
- CypherQueryBuilder: tenant-scoped builder for node/edge CRUD and simple
  MATCH queries, emitting the SQL wrapper AGE requires.
- RevenueAnalyticsQueries: canned analytics queries over the revenue graph.
"""

from __future__ import annotations

import json
from typing import Any, Optional, Union
from uuid import UUID

from app.services.knowledge_graph.models import EdgeLabel, NodeLabel

__all__ = ["CypherQueryBuilder", "RevenueAnalyticsQueries"]

GRAPH_NAME = "stratum_knowledge_graph"


def _cypher_value(value: Any) -> str:
    """Render a Python value as a Cypher literal."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    # Strings (and everything else) are JSON-escaped then single-quoted
    escaped = json.dumps(str(value))[1:-1].replace("'", "\\'")
    return f"'{escaped}'"


def _cypher_map(properties: dict[str, Any]) -> str:
    """Render a dict as a Cypher map literal."""
    items = ", ".join(f"{key}: {_cypher_value(value)}" for key, value in properties.items())
    return "{" + items + "}"


def _wrap(cypher: str, graph_name: str = GRAPH_NAME) -> str:
    """Wrap a Cypher statement in the SQL AGE requires."""
    return (
        f"SELECT * FROM cypher('{graph_name}', $$\n"
        f"    {cypher}\n"
        f"$$) AS (result agtype);"
    )


class CypherQueryBuilder:
    """
    Tenant-scoped Cypher query builder for Apache AGE.

    Supports both one-shot builders (build_create_node/build_merge_node/
    build_create_edge returning full SQL strings) and a small fluent API
    (match_node().return_fields().limit().build()).
    """

    GRAPH_NAME = GRAPH_NAME

    def __init__(self, tenant_id: Union[UUID, str, int]):
        self.tenant_id = str(tenant_id)
        self._match_clauses: list[str] = []
        self._where_clauses: list[str] = []
        self._return_fields: list[str] = ["*"]
        self._order_by: Optional[str] = None
        self._limit: Optional[int] = None

    # -------------------------------------------------------------------
    # One-shot builders
    # -------------------------------------------------------------------

    def _with_tenant(self, properties: Optional[dict[str, Any]]) -> dict[str, Any]:
        """Ensure tenant_id is present in a property map (tenant isolation)."""
        props = dict(properties or {})
        props.setdefault("tenant_id", self.tenant_id)
        return props

    def build_create_node(
        self,
        alias: str,
        node_label: NodeLabel,
        properties: dict[str, Any],
    ) -> str:
        """Build SQL that creates a node and returns its properties."""
        props = self._with_tenant(properties)
        cypher = (
            f"CREATE ({alias}:{node_label.value} {_cypher_map(props)}) "
            f"RETURN properties({alias})"
        )
        return _wrap(cypher)

    def build_merge_node(
        self,
        alias: str,
        node_label: NodeLabel,
        match_properties: dict[str, Any],
        set_properties: dict[str, Any],
    ) -> str:
        """Build SQL that merges (upserts) a node and returns its properties."""
        match_props = self._with_tenant(match_properties)
        set_clause = ""
        if set_properties:
            assignments = ", ".join(
                f"{alias}.{key} = {_cypher_value(value)}"
                for key, value in set_properties.items()
            )
            set_clause = f" SET {assignments}"
        cypher = (
            f"MERGE ({alias}:{node_label.value} {_cypher_map(match_props)})"
            f"{set_clause} RETURN properties({alias})"
        )
        return _wrap(cypher)

    def build_create_edge(
        self,
        start_label: NodeLabel,
        start_match: dict[str, Any],
        edge_label: EdgeLabel,
        end_label: NodeLabel,
        end_match: dict[str, Any],
        edge_properties: Optional[dict[str, Any]] = None,
    ) -> str:
        """Build SQL that creates an edge between two matched nodes."""
        edge_props = f" {_cypher_map(edge_properties)}" if edge_properties else ""
        cypher = (
            f"MATCH (a:{start_label.value} {_cypher_map(self._with_tenant(start_match))}), "
            f"(b:{end_label.value} {_cypher_map(self._with_tenant(end_match))}) "
            f"CREATE (a)-[r:{edge_label.value}{edge_props}]->(b) "
            f"RETURN properties(r)"
        )
        return _wrap(cypher)

    def build_delete_node(self, node_label: NodeLabel, match: dict[str, Any]) -> str:
        """Build SQL that detach-deletes matching nodes."""
        cypher = (
            f"MATCH (n:{node_label.value} {_cypher_map(self._with_tenant(match))}) "
            f"DETACH DELETE n RETURN count(n) AS deleted"
        )
        return _wrap(cypher)

    # -------------------------------------------------------------------
    # Fluent API
    # -------------------------------------------------------------------

    def match_node(
        self,
        alias: str,
        label: NodeLabel,
        properties: Optional[dict[str, Any]] = None,
    ) -> "CypherQueryBuilder":
        """Add a MATCH clause for a node pattern (tenant filter included)."""
        props = self._with_tenant(properties)
        self._match_clauses.append(f"({alias}:{label.value} {_cypher_map(props)})")
        return self

    def where(self, condition: str) -> "CypherQueryBuilder":
        """Add a raw WHERE condition."""
        self._where_clauses.append(condition)
        return self

    def return_fields(self, fields: list[str]) -> "CypherQueryBuilder":
        """Set the RETURN clause fields."""
        self._return_fields = list(fields)
        return self

    def order_by(self, expression: str) -> "CypherQueryBuilder":
        """Set an ORDER BY expression."""
        self._order_by = expression
        return self

    def limit(self, count: int) -> "CypherQueryBuilder":
        """Set a LIMIT."""
        self._limit = int(count)
        return self

    def build(self) -> tuple[str, dict[str, Any]]:
        """Build the SQL query; returns (query, params)."""
        cypher = "MATCH " + ", ".join(self._match_clauses or ["(n)"])
        if self._where_clauses:
            cypher += " WHERE " + " AND ".join(self._where_clauses)
        cypher += " RETURN " + ", ".join(self._return_fields)
        if self._order_by:
            cypher += f" ORDER BY {self._order_by}"
        if self._limit is not None:
            cypher += f" LIMIT {self._limit}"
        return _wrap(cypher), {}


class RevenueAnalyticsQueries:
    """Canned analytics queries over the revenue knowledge graph."""

    @staticmethod
    def revenue_by_channel(
        tenant_id: Union[UUID, str], days: int = 30
    ) -> tuple[str, dict[str, Any]]:
        """Revenue totals grouped by acquisition channel."""
        cypher = (
            f"MATCH (c:{NodeLabel.CHANNEL.value} {{tenant_id: '{tenant_id}'}})"
            f"<-[:{EdgeLabel.ATTRIBUTED_TO.value}]-(r:{NodeLabel.REVENUE.value}) "
            f"WHERE r.days_ago <= {int(days)} "
            f"RETURN {{channel: c.name, revenue: sum(r.amount), "
            f"conversions: count(r)}}"
        )
        return _wrap(cypher), {"tenant_id": str(tenant_id), "days": days}

    @staticmethod
    def revenue_by_campaign(
        tenant_id: Union[UUID, str],
        platform: Optional[str] = None,
        days: int = 30,
    ) -> tuple[str, dict[str, Any]]:
        """Revenue totals grouped by campaign, optionally filtered by platform."""
        platform_filter = f" AND cp.platform = '{platform}'" if platform else ""
        cypher = (
            f"MATCH (cp:{NodeLabel.CAMPAIGN.value} {{tenant_id: '{tenant_id}'}})"
            f"-[:{EdgeLabel.DROVE.value}]->(r:{NodeLabel.REVENUE.value}) "
            f"WHERE r.days_ago <= {int(days)}{platform_filter} "
            f"RETURN {{campaign: cp.name, platform: cp.platform, "
            f"revenue: sum(r.amount), conversions: count(r)}}"
        )
        return _wrap(cypher), {
            "tenant_id": str(tenant_id),
            "platform": platform,
            "days": days,
        }

    @staticmethod
    def customer_journey(
        tenant_id: Union[UUID, str], profile_external_id: str
    ) -> tuple[str, dict[str, Any]]:
        """Ordered touchpoints and events for a single customer profile."""
        cypher = (
            f"MATCH (p:{NodeLabel.PROFILE.value} "
            f"{{tenant_id: '{tenant_id}', external_id: '{profile_external_id}'}})"
            f"-[:{EdgeLabel.PERFORMED.value}]->(e:{NodeLabel.EVENT.value}) "
            f"RETURN {{profile: p.external_id, event: e.name, "
            f"event_time: e.event_time, properties: properties(e)}}"
        )
        return _wrap(cypher), {
            "tenant_id": str(tenant_id),
            "profile_external_id": profile_external_id,
        }

    @staticmethod
    def segment_revenue_performance(
        tenant_id: Union[UUID, str], days: int = 30
    ) -> tuple[str, dict[str, Any]]:
        """Revenue performance grouped by customer segment."""
        cypher = (
            f"MATCH (s:{NodeLabel.SEGMENT.value} {{tenant_id: '{tenant_id}'}})"
            f"<-[:{EdgeLabel.BELONGS_TO.value}]-(p:{NodeLabel.PROFILE.value})"
            f"-[:{EdgeLabel.GENERATED.value}]->(r:{NodeLabel.REVENUE.value}) "
            f"WHERE r.days_ago <= {int(days)} "
            f"RETURN {{segment: s.name, profiles: count(DISTINCT p), "
            f"revenue: sum(r.amount)}}"
        )
        return _wrap(cypher), {"tenant_id": str(tenant_id), "days": days}

    @staticmethod
    def rfm_segment_trends(tenant_id: Union[UUID, str]) -> tuple[str, dict[str, Any]]:
        """Profile counts per RFM segment."""
        cypher = (
            f"MATCH (p:{NodeLabel.PROFILE.value} {{tenant_id: '{tenant_id}'}}) "
            f"WHERE p.rfm_segment IS NOT NULL "
            f"RETURN {{rfm_segment: p.rfm_segment, profiles: count(p), "
            f"total_revenue: sum(p.total_revenue)}}"
        )
        return _wrap(cypher), {"tenant_id": str(tenant_id)}

    @staticmethod
    def blocked_automations(
        tenant_id: Union[UUID, str], days: int = 30
    ) -> tuple[str, dict[str, Any]]:
        """Automations blocked by the trust gate within the lookback window."""
        cypher = (
            f"MATCH (g:{NodeLabel.TRUST_GATE.value} {{tenant_id: '{tenant_id}'}})"
            f"-[b:{EdgeLabel.BLOCKED.value}]->(a:{NodeLabel.AUTOMATION.value}) "
            f"WHERE b.days_ago <= {int(days)} "
            f"RETURN {{automation: a.name, action_type: a.action_type, "
            f"reason: b.reason, signal_health: b.signal_health}}"
        )
        return _wrap(cypher), {"tenant_id": str(tenant_id), "days": days}

    @staticmethod
    def signal_health_impact(
        tenant_id: Union[UUID, str], days: int = 30
    ) -> tuple[str, dict[str, Any]]:
        """Relationship between signal health scores and revenue outcomes."""
        cypher = (
            f"MATCH (h:{NodeLabel.HEALTH_SCORE.value} {{tenant_id: '{tenant_id}'}})"
            f"<-[:{EdgeLabel.HAS_HEALTH.value}]-(acc:{NodeLabel.ACCOUNT.value})"
            f"-[:{EdgeLabel.PRODUCED.value}]->(r:{NodeLabel.REVENUE.value}) "
            f"WHERE r.days_ago <= {int(days)} "
            f"RETURN {{account: acc.external_id, health_score: h.score, "
            f"status: h.status, revenue: sum(r.amount)}}"
        )
        return _wrap(cypher), {"tenant_id": str(tenant_id), "days": days}

    @staticmethod
    def multi_touch_attribution_paths(
        tenant_id: Union[UUID, str],
        min_touchpoints: int = 2,
        limit: int = 50,
    ) -> tuple[str, dict[str, Any]]:
        """Common multi-touch conversion paths across touchpoints."""
        cypher = (
            f"MATCH path = (p:{NodeLabel.PROFILE.value} {{tenant_id: '{tenant_id}'}})"
            f"-[:{EdgeLabel.RECEIVED.value}*{int(min_touchpoints)}..10]->"
            f"(t:{NodeLabel.TOUCHPOINT.value}) "
            f"RETURN {{profile: p.external_id, path_length: length(path)}} "
            f"LIMIT {int(limit)}"
        )
        return _wrap(cypher), {
            "tenant_id": str(tenant_id),
            "min_touchpoints": min_touchpoints,
            "limit": limit,
        }
