
def run_pipeline() -> None:
    """
    config = load_config()
    rows = collect(config)
    rows = label(rows, config["label_mode"])
    save_run(rows); save_labels(rows)
    export(rows)
    Логирует каждый этап. Любой сбой этапа логируется, пайплайн пытается
    дойти до export с тем что собрано.
    """
