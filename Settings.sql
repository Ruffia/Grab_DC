/*
 Navicat SQLite Dump SQL

 Source Server         : Tip
 Source Server Type    : SQLite
 Source Server Version : 3045000 (3.45.0)
 Source Schema         : main

 Target Server Type    : SQLite
 Target Server Version : 3045000 (3.45.0)
 File Encoding         : 65001

 Date: 13/11/2026 21:18:05
*/

PRAGMA foreign_keys = false;

-- ----------------------------
-- Table structure for Settings
-- ----------------------------
DROP TABLE IF EXISTS "Settings";
CREATE TABLE "Settings" (
  "ItemName" TEXT NOT NULL,
  "ItemValue" TEXT,
  "Description" TEXT,
  PRIMARY KEY ("ItemName")
);

-- ----------------------------
-- Records of Settings
-- ----------------------------
INSERT INTO "Settings" VALUES ('Days', '3', '获取多少天(交易日)的资金流数据');

PRAGMA foreign_keys = true;
