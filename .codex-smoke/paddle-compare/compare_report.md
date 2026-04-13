# PaddleOCR vs Current Local Path

Сравнение локального движка PaddleOCR с текущим Gemini-first контуром.

## image001.png

- allowed_labels: 26, 27
- current_found: 26, 26, 27
- paddle_found: 26, 26, 27
- current_missing_vs_allowed: none
- paddle_missing_vs_allowed: none
- current_elapsed_seconds: 25.50455879999936
- paddle_elapsed_seconds: 3.6279649999996764
- paddle_ok: True
- paddle_error: none

## page4.png

- allowed_labels: 13, 9, 4, 11, 10, 3, 8, 6, 1, 7, 5, 2, 14-1, 14-2, 14-3, 14-4(1), 14-4(2)
- current_found: 13, 9, 4, 11, 8, 10, 6, 1, 3, 7, 5, 2, 14-1, 14-2, 14-3, 14-4(1), 14-4(2)
- paddle_found: 1, 13, 9, 4, 11, 8, 10, 6, 60, 1, 3, 7, 5, 2, 14-1, 14-2, 14-3, 14-4(2), 4
- current_missing_vs_allowed: none
- paddle_missing_vs_allowed: 14-4(1)
- current_elapsed_seconds: 216.6106020999996
- paddle_elapsed_seconds: 40.767023399999744
- paddle_ok: True
- paddle_error: none

## test1.jpg

- allowed_labels: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42
- current_found: none
- paddle_found: 8, 37, 40, 40, 18, 39, 26, 4, 33, 33, 36
- current_missing_vs_allowed: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42
- paddle_missing_vs_allowed: 1, 2, 3, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15, 16, 17, 19, 20, 21, 22, 23, 24, 25, 27, 28, 31, 32, 34, 35, 38, 41, 42
- current_elapsed_seconds: 43.79329470000084
- paddle_elapsed_seconds: 18.723665199999232
- paddle_ok: True
- paddle_error: none
