"""Native identity views preserve legacy binding storage and immutable scopes."""


def read(c, delivery, generation):
    return c.execute(
        """SELECT b.*,COALESCE(s.binding_kind,
           CASE WHEN b.work IS NULL THEN 'coordination' ELSE 'work' END) AS binding_kind,
           s.role_revision FROM hb_native_goal_binding b
           LEFT JOIN hb_native_goal_scope s USING(delivery,generation)
           WHERE b.delivery=%s AND b.generation=%s""",
        (delivery, generation),
    ).fetchone()
