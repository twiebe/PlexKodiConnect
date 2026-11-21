-- schema of Kodi 21.2 Textures13.db, for tests
CREATE TABLE path (id integer primary key, url text, type text, texture text);
CREATE TABLE sizes (idtexture integer, size integer, width integer, height integer, usecount integer, lastusetime text);
CREATE TABLE texture (id integer primary key, url text, cachedurl text, imagehash text, lasthashcheck text);
CREATE TABLE version (idVersion integer, iCompressCount integer);
CREATE INDEX idxPath ON path(url, type);
CREATE INDEX idxSize ON sizes(idtexture, size);
CREATE INDEX idxSize2 ON sizes(idtexture, width, height);
CREATE INDEX idxTexture ON texture(url);
CREATE TRIGGER textureDelete AFTER delete ON texture FOR EACH ROW BEGIN delete from sizes where sizes.idtexture=old.id; END;
