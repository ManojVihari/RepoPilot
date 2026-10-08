package com.shop.dto;

public record CreateOrderRequest(
    @NotBlank @Email String customerEmail,
    @NotEmpty @Size(max = 20) List<LineItem> lines,
    PaymentMethod paymentMethod
) {}
