
CREATE TABLE IF NOT EXISTS `users` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '用户ID',
  `username` VARCHAR(50) NOT NULL COMMENT '用户名',
  `phone` VARCHAR(20) DEFAULT NULL COMMENT '手机号(可能为空或脱敏)',
  `register_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '注册时间',
  `last_login_time` DATETIME DEFAULT NULL COMMENT '最后登录时间',
  `user_tags` JSON DEFAULT NULL COMMENT '用户标签数组,如["vip","high_value"]',
  `status` TINYINT NOT NULL DEFAULT 1 COMMENT '状态:1-正常,2-冻结,3-注销',
  PRIMARY KEY (`id`),
  INDEX `idx_register_time` (`register_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户主表';

CREATE TABLE IF NOT EXISTS `categories` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '类目ID',
  `name` VARCHAR(100) NOT NULL COMMENT '类目名称',
  `parent_id` INT UNSIGNED DEFAULT 0 COMMENT '父类目ID,0表示一级类目',
  `level` TINYINT NOT NULL DEFAULT 1 COMMENT '层级:1-一级,2-二级',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='商品类目表';

CREATE TABLE IF NOT EXISTS `products` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '商品ID',
  `name` VARCHAR(200) NOT NULL COMMENT '商品名称',
  `category_id` INT UNSIGNED NOT NULL COMMENT '所属类目ID',
  `price` DECIMAL(10,2) NOT NULL COMMENT '单价',
  `stock` INT NOT NULL DEFAULT 0 COMMENT '库存',
  `is_on_sale` TINYINT NOT NULL DEFAULT 1 COMMENT '是否上架:1-是,0-否',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  INDEX `idx_category_id` (`category_id`),
  CONSTRAINT `fk_product_category` FOREIGN KEY (`category_id`) REFERENCES `categories`(`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='商品信息表';

CREATE TABLE IF NOT EXISTS `orders` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '订单ID',
  `user_id` BIGINT UNSIGNED NOT NULL COMMENT '下单用户ID',
  `order_amount` DECIMAL(12,2) NOT NULL COMMENT '订单原价总额',
  `pay_amount` DECIMAL(12,2) NOT NULL COMMENT '实际支付金额(扣除优惠)',
  `status` TINYINT NOT NULL DEFAULT 0 COMMENT '订单状态:0-待支付,1-已支付,2-已发货,3-已完成,4-已取消,5-已退款',
  `pay_time` DATETIME DEFAULT NULL COMMENT '支付时间',
  `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '下单时间',
  `shipping_address` VARCHAR(500) DEFAULT NULL COMMENT '收货地址(可能为空字符串)',
  PRIMARY KEY (`id`),
  INDEX `idx_user_id` (`user_id`),
  INDEX `idx_create_time` (`create_time`),
  INDEX `idx_status_pay_time` (`status`, `pay_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='订单主表';

CREATE TABLE IF NOT EXISTS `order_items` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `order_id` BIGINT UNSIGNED NOT NULL COMMENT '订单ID',
  `product_id` BIGINT UNSIGNED NOT NULL COMMENT '商品ID',
  `quantity` INT NOT NULL DEFAULT 1 COMMENT '购买数量',
  `unit_price` DECIMAL(10,2) NOT NULL COMMENT '成交单价(快照)',
  `refund_amount` DECIMAL(10,2) DEFAULT 0.00 COMMENT '退款金额,0表示未退款',
  PRIMARY KEY (`id`),
  INDEX `idx_order_id` (`order_id`),
  CONSTRAINT `fk_item_order` FOREIGN KEY (`order_id`) REFERENCES `orders`(`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='订单明细表';






SET SESSION cte_max_recursion_depth = 50000;

INSERT INTO `categories` (`id`, `name`, `parent_id`, `level`) VALUES
(1,'电子产品',0,1),(2,'手机',1,2),(3,'笔记本电脑',1,2),(4,'平板',1,2),
(5,'家居用品',0,1),(6,'厨房小电',5,2),(7,'收纳整理',5,2),(8,'家纺',5,2),
(9,'服饰鞋包',0,1),(10,'男装',9,2),(11,'女装',9,2),(12,'运动鞋服',9,2),
(13,'美妆个护',0,1),(14,'护肤',13,2),(15,'彩妆',13,2),(16,'洗护',13,2),
(17,'食品生鲜',0,1),(18,'零食饮料',17,2),(19,'粮油调味',17,2),(20,'生鲜水果',17,2);



-- ==========================================
-- 2. 生成用户 (2000条, 含VIP倾斜+脏数据)
-- ==========================================
INSERT INTO `users` (`id`, `username`, `phone`, `register_time`, `last_login_time`, `user_tags`, `status`)
WITH RECURSIVE seq AS (
    SELECT 1 AS n UNION ALL SELECT n+1 FROM seq WHERE n < 2000
)
SELECT
    n + 1000 AS id,
    CONCAT('user_', LPAD(n, 5, '0')) AS username,
    -- 5%用户手机号为NULL, 3%为空字符串
    CASE
        WHEN n % 20 = 0 THEN NULL
        WHEN n % 33 = 0 THEN ''
        ELSE CONCAT('1', LPAD(FLOOR(3000000000 + RAND()*6999999999), 10, '0'))
    END AS phone,
    -- 注册时间分散在过去18个月
    DATE_SUB('2026-07-26 00:00:00', INTERVAL FLOOR(RAND()*540) DAY)
        + INTERVAL FLOOR(RAND()*86400) SECOND AS register_time,
    -- 10%用户从未登录
    CASE WHEN n % 10 = 0 THEN NULL
         ELSE DATE_SUB('2026-07-26 00:00:00', INTERVAL FLOOR(RAND()*90) DAY)
              + INTERVAL FLOOR(RAND()*86400) SECOND
    END AS last_login_time,
    -- 标签: 前5%为VIP, 接下来15%为high_value
    CASE
        WHEN n <= 100 THEN '["vip","high_value","early_adopter"]'
        WHEN n <= 400 THEN '["high_value"]'
        WHEN n <= 600 THEN '["regular","active"]'
        ELSE '["regular"]'
    END AS user_tags,
    -- 状态分布: 90%正常, 5%冻结, 5%注销
    CASE WHEN n % 20 = 0 THEN 2 WHEN n % 20 = 1 THEN 3 ELSE 1 END AS status
FROM seq;

-- ==========================================
-- 3. 生成商品 (500条, 价格/库存有业务逻辑)
-- ==========================================
INSERT INTO `products` (`id`, `name`, `category_id`, `price`, `stock`, `is_on_sale`)
WITH RECURSIVE seq AS (
    SELECT 1 AS n UNION ALL SELECT n+1 FROM seq WHERE n < 500
)
SELECT
    n + 2000 AS id,
    CONCAT(
        ELT(1 + FLOOR(RAND()*6), '经典','新款','限定','联名','升级','基础'),
        ELT(1 + FLOOR(RAND()*8), '手机','笔记本','空气炸锅','电饭煲','T恤','跑鞋','面霜','坚果'),
        ' ', CHAR(65 + FLOOR(RAND()*26)), LPAD(FLOOR(RAND()*999), 3, '0')
    ) AS name,
    -- 均匀分配到20个类目
    1 + FLOOR(RAND()*20) AS category_id,
    -- 价格: 电子类偏高(1000-15000), 其他偏低(10-500)
    CASE WHEN 1+FLOOR(RAND()*20) IN (1,2,3,4)
         THEN ROUND(1000 + RAND()*14000, 2)
         ELSE ROUND(10 + RAND()*490, 2)
    END AS price,
    -- 库存: 热门商品(stock>100)占20%, 缺货占5%
    CASE WHEN n % 5 = 0 THEN 0
         WHEN n % 5 = 1 THEN FLOOR(100 + RAND()*900)
         ELSE FLOOR(1 + RAND()*99)
    END AS stock,
    -- 10%下架
    IF(n % 10 = 0, 0, 1) AS is_on_sale
FROM seq;

-- ==========================================
-- 4. 生成订单 (30000条, 核心: 数据倾斜+时间分布)
-- ==========================================
INSERT INTO `orders` (
    `id`, `user_id`, `order_amount`, `pay_amount`,
    `status`, `pay_time`, `create_time`, `shipping_address`
)
WITH RECURSIVE seq AS (
    SELECT 1 AS n
    UNION ALL
    SELECT n+1 FROM seq WHERE n < 30000
)
SELECT
    n + 10000 AS id,
    CASE WHEN rnd_user < 0.4
         THEN 1001 + FLOOR(rnd_user2 * 100)
         ELSE 1101 + FLOOR(rnd_user2 * 1900)
    END AS user_id,

    ROUND(EXP(5.5 + rnd_amt1 * 2.5), 2) AS order_amount,
    ROUND(EXP(5.5 + rnd_amt2 * 2.5) * (0.7 + rnd_discount * 0.3), 2) AS pay_amount,

    CASE
        WHEN rnd_status < 0.08 THEN 0   -- 8%
        WHEN rnd_status < 0.20 THEN 1   -- 12%
        WHEN rnd_status < 0.35 THEN 2   -- 15%
        WHEN rnd_status < 0.90 THEN 3   -- 55%
        WHEN rnd_status < 0.95 THEN 4   -- 5%
        ELSE 5                          -- 5%
    END AS status,

    CASE WHEN rnd_status >= 0.08
         THEN DATE_SUB('2026-07-26 22:00:00',
              INTERVAL FLOOR(rnd_pay * 180) DAY)
            + INTERVAL FLOOR(rnd_pay * 86400) SECOND
         ELSE NULL
    END AS pay_time,

    DATE_SUB('2026-07-26 22:00:00',
        INTERVAL FLOOR(POW(rnd_create, 0.6) * 180) DAY
    ) + INTERVAL FLOOR(rnd_create * 86400) SECOND AS create_time,

    CASE
        WHEN n % 33 = 0 THEN NULL
        WHEN n % 50 = 0 THEN ''
        ELSE CONCAT(
            ELT(1+FLOOR(rnd_addr * 5),'北京市','上海市','广州市','深圳市','杭州市'),
            ELT(1+FLOOR(rnd_addr * 4),'朝阳区','浦东新区','天河区','南山区'),
            '测试路', FLOOR(rnd_addr * 999), '号'
        )
    END AS shipping_address

FROM (
    SELECT
        n,
        RAND() AS rnd_user,
        RAND() AS rnd_user2,
        RAND() AS rnd_amt1,
        RAND() AS rnd_amt2,
        RAND() AS rnd_discount,
        RAND() AS rnd_status,
        RAND() AS rnd_pay,
        RAND() AS rnd_create,
        RAND() AS rnd_addr
    FROM seq
) t;
-- ==========================================
-- 5. 生成订单明细 (~60000条, 每单1-4件)
-- ==========================================
SET FOREIGN_KEY_CHECKS = 0;

INSERT INTO `order_items` (
    `order_id`,
    `product_id`,
    `quantity`,
    `unit_price`,
    `refund_amount`
)
WITH RECURSIVE order_seq AS (
    SELECT 21001 AS order_id
    UNION ALL
    SELECT order_id + 1 FROM order_seq WHERE order_id < 51000
),
item_base AS (
    SELECT
        order_id,
        FLOOR(1 + RAND() * 4) AS item_cnt,
        RAND() AS r_prod,
        RAND() AS r_qty,
        RAND() AS r_price,
        RAND() AS r_refund
    FROM order_seq
),
items AS (
    SELECT
        order_id,
        ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY r_price) AS item_no,
        item_cnt,
        r_prod,
        r_qty,
        r_price,
        r_refund
    FROM item_base
)
SELECT
    order_id,
    100001 + FLOOR(r_prod * 500) AS product_id,
    1 + FLOOR(r_qty * 5) AS quantity,
    ROUND(10 + r_price * 1990, 2) AS unit_price,
    CASE
        WHEN r_refund < 0.05
        THEN ROUND((10 + r_price * 1990) * (1 + FLOOR(r_qty * 5)), 2)
        ELSE 0.00
    END AS refund_amount
FROM items
WHERE item_no <= item_cnt;

SET FOREIGN_KEY_CHECKS = 1;