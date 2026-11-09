/* Emit playlist identifiers and chapter starts in libbluray's 90 kHz timebase. */
#include <inttypes.h>
#include <stdio.h>
#include <libbluray/bluray.h>

int main(int argc, char **argv) {
    if (argc != 2) {
        fputs("Usage: inosculate-bluray-catalog <disc directory or ISO>\n", stderr);
        return 2;
    }
    BLURAY *disc = bd_open(argv[1], NULL);
    if (!disc) {
        fputs("Cannot open Blu-ray navigation data\n", stderr);
        return 1;
    }
    uint32_t count = bd_get_titles(disc, TITLES_ALL, 0);
    int emitted = 0;
    fputs("[", stdout);
    for (uint32_t index = 0; index < count; index++) {
        BLURAY_TITLE_INFO *info = bd_get_title_info(disc, index, 0);
        if (!info) continue;
        if (info->duration > 0) {
            printf("%s{\"playlist\":%u,\"ticks\":%" PRIu64 ",\"starts\":[",
                   emitted ? "," : "", info->playlist, info->duration);
            for (uint32_t i = 0; i < info->chapter_count; i++) {
                printf("%s%" PRIu64, i ? "," : "", info->chapters[i].start);
            }
            fputs("]}", stdout);
            emitted++;
        }
        bd_free_title_info(info);
    }
    puts("]");
    bd_close(disc);
    if (!emitted) fputs("No readable Blu-ray playlists\n", stderr);
    return emitted ? 0 : 1;
}
