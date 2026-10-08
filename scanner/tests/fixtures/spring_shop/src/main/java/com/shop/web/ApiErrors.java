package com.shop.web;

@RestControllerAdvice
public class ApiErrors {
    @ExceptionHandler(PaymentDeclinedException.class)
    @ResponseStatus(HttpStatus.PAYMENT_REQUIRED)
    public String declined(PaymentDeclinedException e) { return "declined"; }
}
