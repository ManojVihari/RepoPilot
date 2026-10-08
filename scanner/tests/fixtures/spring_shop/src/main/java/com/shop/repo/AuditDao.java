package com.shop.repo;

@Repository
public class AuditDao {
    private final JdbcTemplate jdbcTemplate;

    public AuditDao(JdbcTemplate jdbcTemplate) { this.jdbcTemplate = jdbcTemplate; }

    public void record(Long orderId, String action) {
        jdbcTemplate.update("INSERT INTO audit_log (order_id, action) VALUES (?, ?)", orderId, action);
    }
}
