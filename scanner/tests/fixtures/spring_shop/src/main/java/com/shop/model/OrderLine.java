package com.shop.model;

@Entity
public class OrderLine {
    @Id
    private Long id;
    private String sku;
    private int quantity;
    @ManyToOne
    private Order order;
}
