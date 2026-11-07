/*
 Navicat SQLite Dump SQL

 Source Server         : Tip
 Source Server Type    : SQLite
 Source Server Version : 3045000 (3.45.0)
 Source Schema         : main

 Target Server Type    : SQLite
 Target Server Version : 3045000 (3.45.0)
 File Encoding         : 65001

 Date: 07/11/2026 10:31:41
*/

PRAGMA foreign_keys = false;

-- ----------------------------
-- Table structure for FocusStock
-- ----------------------------
DROP TABLE IF EXISTS "FocusStock";
CREATE TABLE "FocusStock" (
  "FocusDate" DATE NOT NULL,
  "StockCode" TEXT NOT NULL,
  "StockName" TEXT,
  PRIMARY KEY ("FocusDate", "StockCode")
);

PRAGMA foreign_keys = true;
