package com.shop.repo;

public interface OrderRepository extends JpaRepository<Order, Long> {
    List<Order> findByCustomerEmail(String email);

    @Modifying
    @Query("update Order o set o.status = :status where o.id = :id")
    int updateStatus(Long id, OrderStatus status);
}
