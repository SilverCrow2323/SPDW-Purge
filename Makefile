PKG      := spdw-purge
VERSION  := $(shell sed -n 's/^VER = "\(.*\)"/\1/p' spdw_purge/core.py)
PREFIX   ?= /usr
DESTDIR  ?=
DEB      := dist/$(PKG)_$(VERSION)_all.deb
LIB      := $(DESTDIR)$(PREFIX)/share/$(PKG)

.PHONY: help run test install uninstall deb screenshots clean
help:
	@echo "targets: run test install uninstall deb screenshots clean"

run:
	python3 -m spdw_purge

test:
	python3 -m unittest discover -s tests -v
	python3 -m spdw_purge --selftest

install:
	install -d $(LIB)/spdw_purge
	install -m 644 spdw_purge/*.py $(LIB)/spdw_purge/
	install -D -m 755 packaging/$(PKG) $(DESTDIR)$(PREFIX)/bin/$(PKG)
	install -D -m 644 packaging/$(PKG).desktop $(DESTDIR)$(PREFIX)/share/applications/$(PKG).desktop
	install -D -m 644 packaging/$(PKG).svg $(DESTDIR)$(PREFIX)/share/icons/hicolor/scalable/apps/$(PKG).svg
	install -D -m 644 packaging/$(PKG).1 $(DESTDIR)$(PREFIX)/share/man/man1/$(PKG).1
	gzip -9nf $(DESTDIR)$(PREFIX)/share/man/man1/$(PKG).1
	install -D -m 644 LICENSE $(DESTDIR)$(PREFIX)/share/doc/$(PKG)/copyright

uninstall:
	rm -rf $(PREFIX)/share/$(PKG) $(PREFIX)/bin/$(PKG) $(PREFIX)/share/applications/$(PKG).desktop \
	       $(PREFIX)/share/icons/hicolor/scalable/apps/$(PKG).svg $(PREFIX)/share/man/man1/$(PKG).1.gz \
	       $(PREFIX)/share/doc/$(PKG)

deb:
	rm -rf build/root && mkdir -p build/root/DEBIAN dist
	$(MAKE) install DESTDIR=$(CURDIR)/build/root
	find build/root -name __pycache__ -prune -exec rm -rf {} +
	sed 's/@VERSION@/$(VERSION)/' packaging/control.in > build/root/DEBIAN/control
	install -m 755 packaging/postinst packaging/postrm build/root/DEBIAN/
	dpkg-deb --root-owner-group --build build/root $(DEB)
	@echo "built $(DEB)"

screenshots:
	xvfb-run -a -s "-screen 0 1280x900x24" python3 tools/screenshots.py assets/screenshots

clean:
	rm -rf build dist
