import PyQt6.QtCore as QtCore
from PyQt6.QtGui import QIntValidator
from PyQt6.QtWidgets import *
import functools
import time
import sys
import argparse
import json
import re


'''
Assembles a Django model from a JSON input. Deduces types. 
Allows changing of field names and creates a conversion function to read in field names.
'''


def errorFn(message):
    print(message)
    exit(0)


class ConfigurationException(Exception):
    pass


class FieldType:
    NestedObject = "Nested Object"
    ObjectArray = "ObjectArray"
    PrimitiveArray = "PrimitiveArray"
    # Means long string, will be TextField unless max_length is set, then it's CharField.
    String = "String"
    Int = "Int"
    Decimal = "Decimal"
    Float = "Float"
    Empty = "Empty"
    Url = "Url"
    Boolean = "Boolean"
    Json = "Json"

    @classmethod
    def isNumber(cls, value):
        return value == cls.Decimal or value == cls.Int or value == cls.Float

    @classmethod
    def isString(cls, value):
        return value == cls.VarChar or value == cls.TextField


def fieldTypeIsNestedObject(value):
    return isinstance(value, dict)


def fieldTypeIsString(value):
    return isinstance(value, str)


def fieldTypeIsInt(value):
    return isinstance(value, int)


def fieldTypeIsDecimal(value):
    return isinstance(value, float)


def fieldTypeIsObjectArray(value):
    return isinstance(value, list) and (len(value) > 0 and isinstance(value[0], dict))


def fieldTypeIsPrimitiveArray(value):
    return isinstance(value, list) and not fieldTypeIsObjectArray(value)


def fieldTypeIsEmpty(value):
    return value == None


def fieldTypeIsBoolean(value):
    return isinstance(value, bool)


def fieldTypeIsUrl(value):
    if not isinstance(value, str):
        return False
    # https://stackoverflow.com/a/48689681
    expr = "((http|https)\:\/\/)?[a-zA-Z0-9\.\/\?\:@\-_=#]+\.([a-zA-Z]){2,6}([a-zA-Z0-9\.\&\/\?\:@\-_=#])*"
    return re.match(expr, value) is not None


def guessFieldFromSingleSample(value):
    if fieldTypeIsBoolean(value):
        return FieldType.Boolean
    if fieldTypeIsNestedObject(value):
        return FieldType.NestedObject
    if fieldTypeIsUrl(value):  # important that this comes before string types
        return FieldType.Url
    if fieldTypeIsString(value):
        return FieldType.String
    if fieldTypeIsDecimal(value):
        return FieldType.Decimal
    if fieldTypeIsInt(value):
        return FieldType.Int
    if fieldTypeIsEmpty(value):
        return FieldType.Empty
    if fieldTypeIsPrimitiveArray(value):
        return FieldType.PrimitiveArray
    if fieldTypeIsObjectArray(value):
        return FieldType.ObjectArray

    #errorFn(f"Couldn't understand field value {value}; this shouldn't be possible.")
    return FieldType.Json


def guessFieldType(values):
    field_values = map(guessFieldFromSingleSample, values)
    num_empty = 0

    def removeEmpty(value):
        nonlocal num_empty
        if value == FieldType.Empty:
            num_empty += 1
            return False
        else:
            return True
    field_values = filter(removeEmpty, field_values)
    field_values = list(field_values)
    if len(field_values) == 0:
        return FieldType.Json, len(values), True
    counts = dict()
    for val in field_values:
        counts[val] = counts.get(val, 0)+1
    most_prevalent_field = max(counts.items(), key=lambda x: x[1])[0]
    field_result = most_prevalent_field
    optional = num_empty > 0
    return field_result, len(field_values), optional


class Key():
    def __init__(self, json_name, json_values, presence_rate, sample_size):
        #print("key: ",json_name)
        self.sample_size = sample_size
        self.presence_rate = presence_rate
        self.json_name = json_name
        self.json_values = json_values
        self.field_type, self.field_sample_size, self.value_optional = guessFieldType(
            json_values)
        if self.field_type == FieldType.NestedObject:
            self.parseTree = ParseTree(self.json_values)
        if self.field_type == FieldType.ObjectArray:
            len_m = list(map(len, self.json_values))
            max_index = len_m.index(max(len_m))
            self.parseTree = ParseTree(self.json_values[max_index])
        if self.field_type == FieldType.String:
            self.varchar_length = max(map(len, json_values))
        self.config_option = ConfigOption(self)

    def __str__(self):
        return f"key: {self.json_name}"


class ParseTree():
    def __init__(self, input_json):
        key_occurences = {}
        key_values = {}
        if not isinstance(input_json, list):
            input_json = [input_json]
        sample_size = len(input_json)
        for entry in input_json:
            if not isinstance(entry, dict):
                raise Exception("Problem understanding Json")
               # errorFn(f"Where a json object / python dict was expected, the program can't make sense of {entry}")
            for key in entry:
                key_occurences[key] = key_occurences.get(key, 0)+1
                key_values.setdefault(key, []).append(entry[key])
        self.keys: list[Key] = []
        for key in key_occurences:
            key_presence_rate = key_occurences[key]/sample_size
            self.keys.append(
                Key(key, key_values[key], key_presence_rate, sample_size))


class NamingConvention:
    camelCase = "camelCase"
    snake_case = "snake_case"
    none = "None"


def correctName(name):
    global naming_convention
    if " " in name:
        name_separated = name.split(" ")
    elif "_" in name:
        name_separated = name.split("_")
    else:
        name_separated = re.findall('[a-zA-Z][^A-Z]*', name)
    name_separated = [part.lower() for part in name_separated]

    if naming_convention == "camelCase":
        return name_separated[0]+''.join(map(lambda x: x[0].upper()+x[1:].lower(), name_separated[1:]))
    elif naming_convention == "snake_case":
        return '_'.join(name_separated)
    elif naming_convention == "None":
        # django doesn't support __ because it interferes with model lookups
        name = name.replace("__", "_")
    else:
        errorFn("Bad formatting option. This shouldn't be possible.")
    return name


class NestedChoices():
    ForeignKey = "ForeignKey"
    Flatten = "Flatten"
    Json = "Json"


class ConfigOption():
    def __init__(self, key):
        self.key = key
        self.name = correctName(key.json_name)
        self.ignore_field = False  # True if key.field_type == FieldType.Json else
        if key.field_type == FieldType.NestedObject:
            self.choices = [FieldType.NestedObject]
            self.handle_nested_choices = [
                NestedChoices.ForeignKey, NestedChoices.Flatten, NestedChoices.Json]
            self.handle_nested_object_choice = NestedChoices.Flatten
        elif key.field_type == FieldType.PrimitiveArray:
            self.choices = [FieldType.PrimitiveArray]
            self.handle_nested_choices = [NestedChoices.Json]
            self.handle_nested_object_choice = NestedChoices.Json
        elif key.field_type == FieldType.ObjectArray:
            self.choices = [FieldType.ObjectArray]
            self.handle_nested_choices = [
                NestedChoices.ForeignKey, NestedChoices.Json]
            self.handle_nested_object_choice = NestedChoices.ForeignKey
        elif FieldType.isNumber(key.field_type):
            self.choices = [FieldType.Decimal, FieldType.Int,
                            FieldType.Float, FieldType.Json]
        elif key.field_type == FieldType.String or key.field_type == FieldType.Url:
            self.choices = [FieldType.String, FieldType.Url, FieldType.Json]
        elif key.field_type == FieldType.Boolean:
            self.choices = [FieldType.Boolean, FieldType.Json]
        elif key.field_type == FieldType.Json:
            self.choices = [FieldType.Json]
        else:
            raise Exception(f"Field type not accounted for: {key.field_type}")
        self.allow_null_values = self.key.value_optional
        self.field_type = self.key.field_type
        # String settings
        self.max_char = self.key.varchar_length if hasattr(
            self.key, "varchar_length") and self.key.sample_size >= 10 else None
        self.min_char = None
        # Int Settings
        self.min_number = None
        self.max_number = None


FindChildRecursively = QtCore.Qt.FindChildOption.FindChildrenRecursively
TAB = chr(9)
INDENT = TAB
INDENT_2 = TAB*2


class DjangoModelFunctionality():
    def __init__(self):
        self.json_field = None
        self.json_keys = []
        self.fields = []
        self.constructor_args = []
        self.referTo = []

    def addField(self, f):
        self.fields.append(f)

    def JSONFieldConversion(self):
        if self.json_field:
            keys = ', '.join(map(lambda x: '"'+x+'"', self.json_keys))
            return chr(9)*2+f"whitelisted_json = dict([(key,json_data[key]) for key in ({keys})])\n" if self.json_field != None else None
        else:
            return ""

    def nestedModelFields(self):
        if self.referTo:

            output = INDENT_2+"child_models = {}\n"
            for model_name, json_name, isArray in self.referTo:
                output += INDENT_2+f"child_models[\"{model_name}\"] = "
                if isArray:
                    output += f"[{model_name}.fromJSON(item,save) for item in json_data[\"{json_name}\"]]"+"\n"
                else:
                    output += f"{model_name}.fromJSON(json_data[\"{json_name}\"],save)"+"\n"
                    # output.append(chr(9)*2+f"{model_name}.fromJSON(json_data[\"{json_name}\"])"+"\n")
            return output
        else:
            return ""

    def conversionFunction(self):
        def makeArg(arg):
            name, json_name = arg
            return f"{name}=json_data[\"{json_name}\"]"
        paramCode = [makeArg(arg) for arg in self.constructor_args]
        if self.json_field:
            paramCode += ["json_data=whitelisted_json"]
        paramCode = ', '.join(paramCode)
        output = "def fromJSON(json_data: dict,save=False):"+"\n" + \
            self.nestedModelFields() + self.JSONFieldConversion() + chr(9)*2
        output += f"model_instance = MainModel({paramCode})\n"

        output += chr(9)*2+"if save==True: model_instance.save()\n"
        if self.referTo:
            output += INDENT_2+"return model_instance,child_models"+"\n"
        else:
            output += INDENT_2+"return model_instance\n"
        return output

    def __str__(self):
        l = []
        for field in self.fields+([self.json_field] if self.json_field != None else []):
            l.append(f'{chr(9)}{field}\n')
        return f"class {self.modelName}(models.Model):\n"+''.join(l)+chr(9)+self.conversionFunction()+"\n"


class DjangoMainModel(DjangoModelFunctionality):
    def __init__(self):
        super().__init__()
        self.modelName = "MainModel"


class AdditionalModel(DjangoModelFunctionality):
    def __init__(self, name):
        super().__init__()
        self.modelName = name


def jsonDataFieldName():
    return "jsonData" if naming_convention == NamingConvention.camelCase else "json_data"


class DjangoGenerator():
    def makeField(self, key: Key, modelToAddTo):
        config = key.config_option
        json_name = key.json_name
        django_name = config.name
        nullString = "True" if config.allow_null_values else "False"
        if config.ignore_field:
            return
        if key.field_type == FieldType.NestedObject and config.handle_nested_object_choice == NestedChoices.Flatten:
            def joinNest(first, second):
                global naming_convention
                if naming_convention == NamingConvention.camelCase:
                    return first+second[0].upper()+second[1:]
                else:
                    return first+"_"+second
            for nested_key in key.parseTree.keys:
                nested_key.config_option.name = joinNest(
                    django_name, nested_key.config_option.name)
                self.makeField(nested_key, modelToAddTo)
        elif hasattr(config, "handle_nested_object_choice") and config.handle_nested_object_choice == NestedChoices.ForeignKey:
            additionalModel = AdditionalModel(
                name=django_name.capitalize()+"Model")
            if naming_convention == NamingConvention.camelCase:
                field = "mainModel = models.ForeignKey(MainModel,on_delete=models.CASCADE)"
            else:
                field = "main_model = models.ForeignKey(MainModel,on_delete=models.CASCADE)"
            additionalModel.addField(field)
            modelToAddTo.referTo.append(
                (additionalModel.modelName, json_name, key.field_type == FieldType.ObjectArray))
            self.additionalModels.append(additionalModel)
            for nested_key in key.parseTree.keys:
                self.makeField(nested_key, additionalModel)
        elif (hasattr(config, "handle_nested_object_choice") and config.handle_nested_object_choice == NestedChoices.Json) or config.field_type == FieldType.Json:
            if modelToAddTo.json_field is None:
                modelToAddTo.json_field = jsonDataFieldName()+" = " + "models.JSONField()"
            modelToAddTo.json_keys.append(json_name)
        else:
            if key.field_type == FieldType.String:
                field = f"{django_name} = "
                if config.max_char:
                    field += f"models.CharField(max_length={config.max_char},null={nullString})"
                else:
                    field += f"models.TextField(null={nullString})"
            elif key.field_type == FieldType.Boolean:
                field = f"{django_name} = models.BooleanField(null={nullString})"
            elif key.field_type == FieldType.Url:
                field = f"{django_name} = models.URLField(null={nullString})"
            elif key.field_type == FieldType.Decimal:
                field = f"{django_name} = models.DecimalField(null={nullString})"
            elif key.field_type == FieldType.Float:
                field = f"{django_name} = models.FloatField(null={nullString})"
            elif key.field_type == FieldType.Int:
                field = f"{django_name} = models.IntegerField(null={nullString})"
            else:
                raise Exception(
                    f"Field type not accounted for {key.field_type}")
            modelToAddTo.addField(field)
            modelToAddTo.constructor_args.append((django_name, json_name))

    def __init__(self, parse_tree: ParseTree):
        self.parse_tree = parse_tree
        self.additionalModels = []
        self.mainModel = DjangoMainModel()
        for key in self.parse_tree.keys:
            self.makeField(key, self.mainModel)

    def import_code(self):
        return 'from django.db import models'

    def __str__(self):
        return self.import_code() + '\n'*2 + str(self.mainModel) + ('\n'.join(map(str, self.additionalModels)))


class MainWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        QApplication.setPalette(QApplication.style().standardPalette())
        QApplication.setStyle(QStyleFactory.create("fusion"))
        layout = QVBoxLayout()
        layout.addWidget(self.paramaterBox())
        button = QPushButton(text="Configure")
        button.clicked.connect(self.generate)
        layout.addWidget(button)
        self.setLayout(layout)

    def makeFileInput(self):
        box = QGroupBox("Input file")
        layout = QHBoxLayout()
        fileButton = QPushButton(text="Select Json Data")
        fileButton.clicked.connect(lambda: self.fileInput.setText(QFileDialog.getOpenFileName(
            caption="Select example json", filter="Json Files (*.json)")[0]))
        self.fileInput = QLineEdit("data.json")

        layout.addWidget(self.fileInput)
        layout.addWidget(fileButton)
        box.setLayout(layout)
        return box

    def makeFileOutput(self):
        box = QGroupBox("Model Output")
        layout = QHBoxLayout()
        fileButton = QPushButton(text="Select")
        fileButton.clicked.connect(lambda: self.fileOutput.setText(QFileDialog.getOpenFileName(
            caption="Model output file", filter="Python Files (*.py)")[0]))
        self.fileOutput = QLineEdit("generated_model.py")

        layout.addWidget(self.fileOutput)
        layout.addWidget(fileButton)
        box.setLayout(layout)
        return box

    def makeSyleInput(self):
        box = QGroupBox("Naming convention")
        box.setLayout(QHBoxLayout())
        for i, option in enumerate(["None", "snake_case", "camelCase"]):
            button = QRadioButton(option)
            button.setChecked(i == 0)
            box.layout().addWidget(button)
        return box

    def paramaterBox(self):
        paramaterBox = QGroupBox("Parameters")
        layout = QVBoxLayout()
        layout.addWidget(self.makeFileInput())
        layout.addWidget(self.makeFileOutput())
        layout.addWidget(self.makeSyleInput())
        paramaterBox.setLayout(layout)
        return paramaterBox

    def alertError(self, msg):
        dialog = QMessageBox(text=msg)
        # dialog.
        dialog.setWindowTitle("Error")
        dialog.exec()
        # self.layout().addWidget(dialog)

    def generate(self):
        file = self.fileInput.text()
        global naming_convention
        naming_convention = [x.text() for x in self.findChildren(
            QRadioButton) if x.isChecked()][0]
        #self.alertError("file not found.")
        try:
            with open(file, errors='ignore') as f:
                fileText = f.read()
                input_json = json.loads(fileText)
        # except json.
        except json.JSONDecodeError as e:
            self.alertError("Error parsing json.")
        except FileNotFoundError as e:
            self.alertError(str(e))
            return
        self.parse_tree = ParseTree(input_json)
        ConfigWindow = ConfigurationWindow(parse_tree=self.parse_tree)
        with open(self.fileOutput.text(), "w") as f:
            f.write(str(DjangoGenerator(self.parse_tree)))


class ConfigurationWindow(QDialog):
    def __init__(self, parse_tree, parent=None):
        super().__init__(parent)
        done_button = QPushButton("Done")
        done_button.clicked.connect(lambda: self.finish())
        self.parse_tree: ParseTree = parse_tree
        self.setLayout(QVBoxLayout())
        self.layout().addWidget(done_button)
        self.layout().addWidget(self.writeConfig())
        self.exec()

    def writeConfig(self):
        configWidget = QWidget()
        configWidget.setLayout(QVBoxLayout())
        # configLayout.setObjectName()
        for key in self.parse_tree.keys:
            configWidget.layout().addWidget(self.makeEntry(key))
        area = QScrollArea()
        area.setWidget(configWidget)
        self.configWidget = configWidget
        return area

    def makeEntry(self, key: Key):
        def typeChanged(button: QRadioButton):
            key.config_option.field_type = button.text()
            entryWidget.parentWidget().layout().replaceWidget(
                entryWidget, self.makeEntry(key))
            entryWidget.deleteLater()

        def handlerChanged(button: QRadioButton, key_ref):
            key_ref.config_option.handle_nested_object_choice = button.text()
            print(key_ref)
            self.configWidget.layout().replaceWidget(entryWidget, self.makeEntry(key_ref))
            entryWidget.deleteLater()
            self.update()

        def makeTypePicker():
            group = QGroupBox()
            group.setFlat(True)
            layout = QHBoxLayout()
            group.setLayout(layout)
            for text in config.choices:
                button = QRadioButton(text)
                button.setChecked(config.field_type == text)
                if len(config.choices) == 1:
                    button.setDisabled(True)
                # button.toggled.connect(functools.partial(typeChanged,button))
                button.clicked.connect(functools.partial(typeChanged, button))
                layout.addWidget(button)
            return group

        def makeHandleNestedObjects():
            group = QGroupBox()
            group.setFlat(True)
            layout = QHBoxLayout()
            group.setLayout(layout)
            for text in config.handle_nested_choices:
                button = QRadioButton(text)
                button.setChecked(config.handle_nested_object_choice == text)
                button.clicked.connect(functools.partial(
                    handlerChanged, button, key_ref=key))
                layout.addWidget(button)
            return group

        def handleIgnoreField(status):
            config.ignore_field = status
        config: ConfigOption = key.config_option
        layout = QFormLayout()
        entryWidget = QGroupBox(key.json_name)
        entryWidget.setLayout(layout)
        ignoreField = QCheckBox()
        ignoreField.clicked.connect(handleIgnoreField)
        ignoreField.setChecked(not config.ignore_field)
        layout.addRow("use field", ignoreField)
        allowNull = QCheckBox()
        layout.addRow("database name", QLineEdit(config.name))
        allowNull.setChecked(config.allow_null_values)
        layout.addRow("allow null", allowNull)
        layout.addRow("type", makeTypePicker())

        def makeNumInput():
            widget = QLineEdit()
            widget.setValidator(QIntValidator())
            widget.setValue = lambda x: widget.setText(str(x))
            return widget

        if config.field_type == FieldType.String:
            minLenWidget = makeNumInput()
            if config.min_char:
                minLenWidget.setValue(config.min_char)
            maxLenWidget = makeNumInput()
            # maxLenWidget.setValue(10**1000)
            if config.max_char:
                maxLenWidget.setValue(config.max_char)
            #layout.addRow("min length:",minLenWidget)
            layout.addRow("max length:", maxLenWidget)
        if FieldType.isNumber(config.field_type):
            minValueWidget = makeNumInput()
            if config.min_number:
                minValueWidget.setValue(config.min_number)
            maxValueWidget = makeNumInput()
            if config.max_number:
                maxValueWidget.setValue(config.max_number)
            #layout.addRow("min value",minValueWidget)
            #layout.addRow("max value:",maxValueWidget)
        if config.field_type == FieldType.ObjectArray or config.field_type == FieldType.NestedObject:
            layout.addRow("handle nested object", makeHandleNestedObjects())
            if config.handle_nested_object_choice in (NestedChoices.Flatten, NestedChoices.ForeignKey):
                nestedFieldsWidget = QGroupBox("nested fields")
                nestedFieldsWidget.setLayout(QVBoxLayout())
                for nested_key in key.parseTree.keys:
                    nestedFieldsWidget.layout().addWidget(self.makeEntry(nested_key))
                layout.addWidget(nestedFieldsWidget)
        return entryWidget
        # configWidget.layout().addWidget(entryWidget)

    def readConfig(self):
        pass

    def finish(self):
        config = self.readConfig()
        self.close()


app = QApplication(sys.argv)
mainWindow = MainWindow()
mainWindow.show()
sys.exit(app.exec())
