package com.shop.service;

public interface OrderService {
    OrderResponse create(CreateOrderRequest request);
    OrderResponse get(Long id);
}
