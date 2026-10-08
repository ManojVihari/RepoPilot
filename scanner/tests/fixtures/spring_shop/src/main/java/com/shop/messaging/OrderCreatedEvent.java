package com.shop.messaging;

public record OrderCreatedEvent(Long orderId, String email) {}
