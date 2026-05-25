class ModelRegistry:
    def __init__(self):
        self._models = {}

    def register_model(self, model):
        if model.__name__ != "Model":
            self._models[model.__name__] = model
            from nexorm.relations import install_all_relations

            install_all_relations()

    def get_models(self):
        return list(self._models.values())

    def get_model(self, name):
        return self._models[name]


registry = ModelRegistry()


def register_model(model):
    registry.register_model(model)


def get_models():
    return registry.get_models()


def get_model(name):
    return registry.get_model(name)
