# Синхронизация с upstream (stavultras/router-emulator)

После слияния PR #4 форк намеренно расходится с upstream: `README.md` — наш лендинг
Keenetic Virtual Lab, а не документация upstream. **Кнопка «Sync fork» больше не является
гарантированно безопасным fast-forward**: при расхождении она предложит merge-коммит или
discard наших коммитов. Не пользоваться ею без полной процедуры ниже. Автоматический
merge upstream не настраивается без отдельного решения владельца.

## Процедура

1. **Получить изменения upstream:**

   ```bash
   git remote add upstream https://github.com/stavultras/router-emulator.git  # один раз
   git fetch upstream main
   git log --oneline main..upstream/main        # что нового
   git merge-base --is-ancestor main upstream/main && echo FAST-FORWARD-POSSIBLE
   ```

2. **Проверить изменения upstream** — особое внимание:

   - `qemu-patches/` — новый патч с номером, уже занятым лабораторным (случай 3.9:
     upstream добавил `0059-hw-mips-…`, лабораторная ветка имеет свой `0059-hw-arm-…`).
     Порядковый номер — часть имени файла; коллизия требует перенумерации лабораторного
     патча (например, в `0060-…`) в ветке PR #3, обновления path-фильтров workflow
     (`.github/workflows/ported-*.yml`) и одного прогрева кэша сборки QEMU.
     Сами патчи независимы по файлам (`hw/mips` и `hw/arm/filogic`), `git am` применяет
     их вместе; сломаться может резюм-логика `build.sh` (`patches[have:]` по счётчику
     коммитов) на старых незаново склонированных `src/qemu`.
   - `README.md`, `README.ru.md`, `README.build.*.md` — документация (шаг 5);
   - `VERSION` — выход новой версии upstream;
   - `.github/workflows/` — изменения базового CI.

3. **Подготовить sync-ветку** (никогда не пушить в `main` напрямую при расхождении):

   ```bash
   git checkout -b sync/upstream-<короткий-sha> main
   git merge --ff-only upstream/main   # если fast-forward возможен
   # иначе: git merge upstream/main    # merge-коммит, конфликты — по шагам ниже
   ```

4. **Сохранить наш `README.md`.** При конфликте всегда оставлять нашу версию лендинга:
   `git checkout --ours README.md`.

5. **Перенести изменения upstream `README.md` в `README.upstream.md`:**
   применить diff upstream-изменений к сохранённой английской документации
   (заметка о происхождении в шапке файла остаётся). `README.ru.md` и build-доки
   держать байт-в-байт равными upstream — тогда они мержатся без конфликтов.

6. **Разрешить конфликты QEMU-patches** по шагу 2; убедиться, что оба патча
   (upstream и лабораторный) присутствуют и применяются: свежий клон QEMU +
   `git am qemu-patches/*.patch` должен проходить целиком.

7. **Проверить CI:** дождаться зелёного прогона `Build/release` на sync-ветке/PR;
   отсутствие CI-прогона из-за path-фильтров не считается успешной сборкой.

8. **PR в `main`:** открыть PR из sync-ветки, получить подтверждение владельца,
   merge обычным merge-коммитом (не squash — история upstream сохраняется).

## Быстрая проверка после sync

```bash
git diff --stat main upstream/main                                  # должно быть пусто (кроме наших файлов)
git cat-file -p main:VERSION                                        # версия upstream
diff <(git show upstream/main:README.md | tail -n +2) \
     <(tail -n +9 README.upstream.md | tr -d '\r')                  # тело документации совпадает
```

## Синхронизация с версией 4.1 (27.10.2026)

- Исходный релиз upstream: [v4.1](https://github.com/stavultras/router-emulator/releases/tag/v4.1), коммит `2687edebaf58b2ba92ba2c39e35196ba69265255`.
- Собственный IRQ-патч Filogic перенумерован с 0063 на 0064; upstream 0063 — flash chips by type and ID.
- README.md форка сохранён как лендинг лаборатории, README.upstream.md обновлён английским текстом upstream.
- В `build.yml` сохранены наши фильтры лабораторных workflow, а базовая конфигурация сборки взята из 4.1.
- Параметр CLI `emulator.sh -n` сохранён в upstream 4.1; он передаётся в QEMU как `dump-dir`. Это позволяет сохранить совместимость существующего `lab/run.py --nand-dir`.
- Применение обоих патчей и полная совместимость E2E требуют подтверждения тестами CI. Старые собранные `src/qemu` не использовать для первичной проверки: чистая сборка обязательна.
