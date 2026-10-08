package com.shop.dto;

public class LineItem {
    @NotBlank
    private String sku;
    @Min(1)
    private int quantity;
}
