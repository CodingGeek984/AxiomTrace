"""Runtime helpers appended to generated Go modules.

Go is statically typed, while the Python snippets stored in AxiomTrace nodes
are dynamic. Rather than attempting full type inference, the Go target emits
``any`` values and routes Python semantics through this small helper library.
The helpers are ordinary Go code and are included in every generated file so
the export is self-contained.
"""

from __future__ import annotations

GO_IMPORTS = """import (
\t"encoding/json"
\t"fmt"
\t"io"
\t"math"
\t"math/rand"
\t"net/http"
\t"os"
\t"reflect"
\t"sort"
\t"strconv"
\t"strings"
\t"time"
\t"unicode"
)"""

GO_IMPORT_REFS = """var (
\t_ = json.Marshal
\t_ = fmt.Sprintf
\t_ = io.ReadAll
\t_ = math.Abs
\t_ = rand.Float64
\t_ = http.Get
\t_ = os.Getenv
\t_ = reflect.ValueOf
\t_ = sort.Slice
\t_ = strconv.Itoa
\t_ = strings.TrimSpace
\t_ = time.Now
\t_ = unicode.IsLetter
)"""

GO_RUNTIME = r"""
// --- AxiomTrace runtime helpers ---
// These helpers model Python's dynamic semantics on top of Go's `any` type.

func axiomTruthy(value any) bool {
	if value == nil {
		return false
	}
	switch typed := value.(type) {
	case bool:
		return typed
	case string:
		return typed != ""
	case int:
		return typed != 0
	case int64:
		return typed != 0
	case float64:
		return typed != 0
	case []any:
		return len(typed) > 0
	case map[string]any:
		return len(typed) > 0
	}
	reflected := reflect.ValueOf(value)
	switch reflected.Kind() {
	case reflect.Slice, reflect.Array, reflect.Map:
		return reflected.Len() > 0
	}
	return true
}

func axiomNumber(value any) (float64, bool) {
	switch typed := value.(type) {
	case int:
		return float64(typed), true
	case int8:
		return float64(typed), true
	case int16:
		return float64(typed), true
	case int32:
		return float64(typed), true
	case int64:
		return float64(typed), true
	case uint:
		return float64(typed), true
	case uint8:
		return float64(typed), true
	case uint16:
		return float64(typed), true
	case uint32:
		return float64(typed), true
	case uint64:
		return float64(typed), true
	case float32:
		return float64(typed), true
	case float64:
		return typed, true
	case bool:
		if typed {
			return 1, true
		}
		return 0, true
	case string:
		parsed, err := strconv.ParseFloat(strings.TrimSpace(typed), 64)
		return parsed, err == nil
	}
	reflected := reflect.ValueOf(value)
	switch reflected.Kind() {
	case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64:
		return float64(reflected.Int()), true
	case reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64:
		return float64(reflected.Uint()), true
	case reflect.Float32, reflect.Float64:
		return reflected.Float(), true
	}
	return 0, false
}

func axiomIsInt(value any) bool {
	switch value.(type) {
	case int, int8, int16, int32, int64, uint, uint8, uint16, uint32, uint64, bool:
		return true
	}
	return false
}

func axiomInt(value any) int64 {
	if number, ok := axiomNumber(value); ok {
		return int64(number)
	}
	return 0
}

func axiomFloat(value any) float64 {
	if number, ok := axiomNumber(value); ok {
		return number
	}
	return 0
}

func axiomStr(value any) string {
	if value == nil {
		return "None"
	}
	switch typed := value.(type) {
	case string:
		return typed
	case bool:
		if typed {
			return "True"
		}
		return "False"
	case float64:
		return strconv.FormatFloat(typed, 'g', -1, 64)
	case float32:
		return strconv.FormatFloat(float64(typed), 'g', -1, 32)
	}
	return fmt.Sprint(value)
}

func axiomKey(value any) string {
	if text, ok := value.(string); ok {
		return text
	}
	if integer, ok := value.(int64); ok {
		return strconv.FormatInt(integer, 10)
	}
	return axiomStr(value)
}

func axiomLen(value any) int {
	if value == nil {
		return 0
	}
	reflected := reflect.ValueOf(value)
	if reflected.Kind() == reflect.Ptr {
		if reflected.IsNil() {
			return 0
		}
		reflected = reflected.Elem()
	}
	switch reflected.Kind() {
	case reflect.String, reflect.Slice, reflect.Array, reflect.Map:
		return reflected.Len()
	}
	return 0
}

func axiomIter(value any) []any {
	if value == nil {
		return nil
	}
	switch typed := value.(type) {
	case []any:
		return typed
	case string:
		runes := []rune(typed)
		output := make([]any, len(runes))
		for index, character := range runes {
			output[index] = string(character)
		}
		return output
	case map[string]any:
		keys := make([]string, 0, len(typed))
		for key := range typed {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		output := make([]any, len(keys))
		for index, key := range keys {
			output[index] = key
		}
		return output
	}
	reflected := reflect.ValueOf(value)
	if reflected.Kind() == reflect.Ptr && !reflected.IsNil() {
		reflected = reflected.Elem()
	}
	switch reflected.Kind() {
	case reflect.Slice, reflect.Array:
		output := make([]any, reflected.Len())
		for index := 0; index < reflected.Len(); index++ {
			output[index] = reflected.Index(index).Interface()
		}
		return output
	case reflect.Map:
		keys := reflected.MapKeys()
		output := make([]any, 0, len(keys))
		for _, key := range keys {
			output = append(output, key.Interface())
		}
		return output
	}
	return []any{value}
}

func axiomIndex(value any) (int, bool) {
	number, ok := axiomNumber(value)
	if !ok {
		return 0, false
	}
	return int(number), true
}

func axiomAt(container any, key any) any {
	if container == nil {
		return nil
	}
	switch typed := container.(type) {
	case map[string]any:
		return typed[axiomKey(key)]
	case []any:
		index, ok := axiomIndex(key)
		if !ok {
			return nil
		}
		if index < 0 {
			index += len(typed)
		}
		if index >= 0 && index < len(typed) {
			return typed[index]
		}
		return nil
	case string:
		runes := []rune(typed)
		index, ok := axiomIndex(key)
		if !ok {
			return nil
		}
		if index < 0 {
			index += len(runes)
		}
		if index >= 0 && index < len(runes) {
			return string(runes[index])
		}
		return ""
	}
	return axiomCallMethod(container, "get", key)
}

func axiomSetIndex(container any, key any, value any) any {
	switch typed := container.(type) {
	case map[string]any:
		typed[axiomKey(key)] = value
		return typed
	case []any:
		index, ok := axiomIndex(key)
		if !ok {
			return container
		}
		if index < 0 {
			index += len(typed)
		}
		if index >= 0 && index < len(typed) {
			typed[index] = value
		}
		return typed
	}
	return container
}

func axiomGet(container any, key any, fallback any) any {
	if mapping, ok := container.(map[string]any); ok {
		if value, found := mapping[axiomKey(key)]; found {
			return value
		}
		return fallback
	}
	value := axiomAt(container, key)
	if value == nil {
		return fallback
	}
	return value
}

func axiomContains(container any, item any) bool {
	if container == nil {
		return false
	}
	switch typed := container.(type) {
	case string:
		return strings.Contains(typed, axiomStr(item))
	case []any:
		for _, value := range typed {
			if axiomEq(value, item) {
				return true
			}
		}
		return false
	case map[string]any:
		_, found := typed[axiomKey(item)]
		return found
	}
	reflected := reflect.ValueOf(container)
	if reflected.Kind() == reflect.Ptr && !reflected.IsNil() {
		reflected = reflected.Elem()
	}
	switch reflected.Kind() {
	case reflect.Slice, reflect.Array:
		for index := 0; index < reflected.Len(); index++ {
			if axiomEq(reflected.Index(index).Interface(), item) {
				return true
			}
		}
	case reflect.Map:
		_, found := reflected.Interface().(map[string]any)[axiomKey(item)]
		return found
	case reflect.String:
		return strings.Contains(reflected.String(), axiomStr(item))
	}
	return false
}

func axiomEq(left any, right any) bool {
	if left == nil || right == nil {
		return left == nil && right == nil
	}
	leftNumber, leftOK := axiomNumber(left)
	rightNumber, rightOK := axiomNumber(right)
	if leftOK && rightOK {
		return leftNumber == rightNumber
	}
	return reflect.DeepEqual(left, right)
}

func axiomCompare(left any, right any) int {
	if leftText, ok := left.(string); ok {
		rightText := axiomStr(right)
		switch {
		case leftText < rightText:
			return -1
		case leftText > rightText:
			return 1
		}
		return 0
	}
	leftNumber, leftOK := axiomNumber(left)
	rightNumber, rightOK := axiomNumber(right)
	if leftOK && rightOK {
		switch {
		case leftNumber < rightNumber:
			return -1
		case leftNumber > rightNumber:
			return 1
		}
	}
	return 0
}

func axiomLt(left any, right any) bool { return axiomCompare(left, right) < 0 }
func axiomLe(left any, right any) bool { return axiomCompare(left, right) <= 0 }
func axiomGt(left any, right any) bool { return axiomCompare(left, right) > 0 }
func axiomGe(left any, right any) bool { return axiomCompare(left, right) >= 0 }

func axiomAnd(values ...any) any {
	var result any
	for _, value := range values {
		result = value
		if !axiomTruthy(value) {
			return value
		}
	}
	return result
}

func axiomOr(values ...any) any {
	var result any
	for _, value := range values {
		result = value
		if axiomTruthy(value) {
			return value
		}
	}
	return result
}

func axiomNot(value any) bool { return !axiomTruthy(value) }

func axiomAdd(left any, right any) any {
	if leftText, ok := left.(string); ok {
		if rightText, ok := right.(string); ok {
			return leftText + rightText
		}
	}
	if leftList, ok := left.([]any); ok {
		return append(append([]any{}, leftList...), axiomIter(right)...)
	}
	leftNumber, leftOK := axiomNumber(left)
	rightNumber, rightOK := axiomNumber(right)
	if leftOK && rightOK {
		if axiomIsInt(left) && axiomIsInt(right) {
			return int64(leftNumber) + int64(rightNumber)
		}
		return leftNumber + rightNumber
	}
	return axiomStr(left) + axiomStr(right)
}

func axiomSub(left any, right any) any {
	leftNumber, leftOK := axiomNumber(left)
	rightNumber, rightOK := axiomNumber(right)
	if leftOK && rightOK {
		if axiomIsInt(left) && axiomIsInt(right) {
			return int64(leftNumber) - int64(rightNumber)
		}
		return leftNumber - rightNumber
	}
	return 0
}

func axiomMul(left any, right any) any {
	if text, ok := left.(string); ok {
		return strings.Repeat(text, int(axiomInt(right)))
	}
	if text, ok := right.(string); ok {
		return strings.Repeat(text, int(axiomInt(left)))
	}
	leftNumber, leftOK := axiomNumber(left)
	rightNumber, rightOK := axiomNumber(right)
	if leftOK && rightOK {
		if axiomIsInt(left) && axiomIsInt(right) {
			return int64(leftNumber) * int64(rightNumber)
		}
		return leftNumber * rightNumber
	}
	return 0
}

func axiomDiv(left any, right any) any {
	return axiomFloat(left) / axiomFloat(right)
}

func axiomFloorDiv(left any, right any) any {
	divisor := axiomFloat(right)
	if divisor == 0 {
		return int64(0)
	}
	return int64(math.Floor(axiomFloat(left) / divisor))
}

func axiomMod(left any, right any) any {
	divisor := axiomFloat(right)
	if divisor == 0 {
		return int64(0)
	}
	remainder := math.Mod(axiomFloat(left), divisor)
	if remainder != 0 && (remainder < 0) != (divisor < 0) {
		remainder += divisor
	}
	if axiomIsInt(left) && axiomIsInt(right) {
		return int64(remainder)
	}
	return remainder
}

func axiomPow(left any, right any) any {
	base := axiomFloat(left)
	exponent := axiomFloat(right)
	result := math.Pow(base, exponent)
	if axiomIsInt(left) && axiomIsInt(right) && exponent >= 0 {
		return int64(result)
	}
	return result
}

func axiomNeg(value any) any {
	if axiomIsInt(value) {
		return -axiomInt(value)
	}
	return -axiomFloat(value)
}

func axiomBit(op string, left any, right any) any {
	switch op {
	case "shl":
		return axiomInt(left) << uint(axiomInt(right))
	case "shr":
		return axiomInt(left) >> uint(axiomInt(right))
	case "or":
		return axiomInt(left) | axiomInt(right)
	case "xor":
		return axiomInt(left) ^ axiomInt(right)
	case "and":
		return axiomInt(left) & axiomInt(right)
	}
	return int64(0)
}

func axiomBitNot(value any) any { return ^axiomInt(value) }

func axiomFieldName(name string) string {
	parts := strings.FieldsFunc(name, func(character rune) bool {
		return character == '_' || character == '-'
	})
	if len(parts) == 0 {
		return name
	}
	for index, part := range parts {
		if part == "" {
			continue
		}
		parts[index] = strings.ToUpper(part[:1]) + part[1:]
	}
	return strings.Join(parts, "")
}

func axiomGetAttr(target any, name string) any {
	if target == nil {
		return nil
	}
	if mapping, ok := target.(map[string]any); ok {
		if value, found := mapping[name]; found {
			return value
		}
		return mapping[axiomKey(name)]
	}
	reflected := reflect.ValueOf(target)
	if reflected.Kind() == reflect.Ptr {
		if reflected.IsNil() {
			return nil
		}
		reflected = reflected.Elem()
	}
	if reflected.Kind() == reflect.Struct {
		field := reflected.FieldByName(axiomFieldName(name))
		if field.IsValid() && field.CanInterface() {
			return field.Interface()
		}
	}
	return axiomCallMethod(target, "get", name)
}

func axiomSetAttr(target any, name string, value any) {
	if target == nil {
		return
	}
	if mapping, ok := target.(map[string]any); ok {
		mapping[name] = value
		return
	}
	reflected := reflect.ValueOf(target)
	if reflected.Kind() == reflect.Ptr {
		if reflected.IsNil() {
			return
		}
		reflected = reflected.Elem()
	}
	if reflected.Kind() != reflect.Struct {
		return
	}
	field := reflected.FieldByName(axiomFieldName(name))
	if !field.IsValid() || !field.CanSet() {
		return
	}
	if value == nil {
		field.Set(reflect.Zero(field.Type()))
		return
	}
	source := reflect.ValueOf(value)
	if source.Type().AssignableTo(field.Type()) {
		field.Set(source)
		return
	}
	if source.Type().ConvertibleTo(field.Type()) {
		field.Set(source.Convert(field.Type()))
	}
}

func axiomConvert(value any, target reflect.Type) reflect.Value {
	if target.Kind() == reflect.Interface {
		if value == nil {
			return reflect.Zero(target)
		}
		return reflect.ValueOf(value)
	}
	if value == nil {
		return reflect.Zero(target)
	}
	number, numeric := axiomNumber(value)
	switch target.Kind() {
	case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64:
		if numeric {
			return reflect.ValueOf(int64(number)).Convert(target)
		}
	case reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64:
		if numeric {
			return reflect.ValueOf(uint64(number)).Convert(target)
		}
	case reflect.Float32, reflect.Float64:
		if numeric {
			return reflect.ValueOf(number).Convert(target)
		}
	case reflect.String:
		return reflect.ValueOf(axiomStr(value)).Convert(target)
	case reflect.Bool:
		return reflect.ValueOf(axiomTruthy(value)).Convert(target)
	}
	source := reflect.ValueOf(value)
	if source.Type().AssignableTo(target) {
		return source
	}
	if source.Type().ConvertibleTo(target) {
		return source.Convert(target)
	}
	return reflect.Zero(target)
}

func axiomCallValue(target any, args ...any) any {
	if target == nil {
		return nil
	}
	reflected := reflect.ValueOf(target)
	if reflected.Kind() != reflect.Func || reflected.IsNil() {
		return nil
	}
	kind := reflected.Type()
	fixed := kind.NumIn()
	if kind.IsVariadic() {
		fixed--
	}
	inputs := make([]reflect.Value, 0, len(args))
	for index := 0; index < fixed; index++ {
		if index < len(args) {
			inputs = append(inputs, axiomConvert(args[index], kind.In(index)))
		} else {
			inputs = append(inputs, reflect.Zero(kind.In(index)))
		}
	}
	if kind.IsVariadic() {
		element := kind.In(kind.NumIn() - 1).Elem()
		for index := fixed; index < len(args); index++ {
			inputs = append(inputs, axiomConvert(args[index], element))
		}
	} else {
		for index := fixed; index < len(args) && index < kind.NumIn(); index++ {
			inputs = append(inputs, axiomConvert(args[index], kind.In(index)))
		}
	}
	outputs := reflected.Call(inputs)
	if len(outputs) == 0 {
		return nil
	}
	return outputs[0].Interface()
}

func axiomTitle(text string) string {
	output := []rune(strings.ToLower(text))
	start := true
	for index, character := range output {
		if start && unicode.IsLetter(character) {
			output[index] = unicode.ToUpper(character)
			start = false
		} else if !unicode.IsLetter(character) && !unicode.IsDigit(character) {
			start = true
		}
	}
	return string(output)
}

func axiomAllDigits(text string) bool {
	if text == "" {
		return false
	}
	for _, character := range text {
		if !unicode.IsDigit(character) {
			return false
		}
	}
	return true
}

func axiomStringSlice(value any) []string {
	items := axiomIter(value)
	output := make([]string, len(items))
	for index, item := range items {
		output[index] = axiomStr(item)
	}
	return output
}

func axiomFormat(format string, args ...any) string {
	parts := strings.Split(format, "{}")
	var builder strings.Builder
	for index, part := range parts {
		builder.WriteString(part)
		if index < len(args) {
			builder.WriteString(axiomStr(args[index]))
		}
	}
	return builder.String()
}

func axiomPrint(args ...any) any {
	fmt.Println(args...)
	return nil
}

func axiomCallMethod(target any, name string, args ...any) any {
	if target == nil {
		return nil
	}
	if text, ok := target.(string); ok {
		switch name {
		case "strip":
			return strings.TrimSpace(text)
		case "lstrip":
			return strings.TrimLeft(text, " \t\r\n")
		case "rstrip":
			return strings.TrimRight(text, " \t\r\n")
		case "lower":
			return strings.ToLower(text)
		case "upper":
			return strings.ToUpper(text)
		case "title":
			return axiomTitle(text)
		case "startswith":
			if len(args) > 0 {
				return strings.HasPrefix(text, axiomStr(args[0]))
			}
			return false
		case "endswith":
			if len(args) > 0 {
				return strings.HasSuffix(text, axiomStr(args[0]))
			}
			return false
		case "replace":
			if len(args) >= 2 {
				return strings.ReplaceAll(text, axiomStr(args[0]), axiomStr(args[1]))
			}
			return text
		case "split":
			separator := " "
			if len(args) > 0 {
				separator = axiomStr(args[0])
			}
			parts := strings.Split(text, separator)
			output := make([]any, len(parts))
			for index, part := range parts {
				output[index] = part
			}
			return output
		case "join":
			if len(args) > 0 {
				return strings.Join(axiomStringSlice(args[0]), text)
			}
			return text
		case "isdigit":
			return axiomAllDigits(text)
		case "isspace":
			return text != "" && strings.TrimSpace(text) == ""
		case "format":
			return axiomFormat(text, args...)
		case "encode":
			return []byte(text)
		case "find":
			if len(args) > 0 {
				return int64(strings.Index(text, axiomStr(args[0])))
			}
			return int64(-1)
		case "count":
			if len(args) > 0 {
				return int64(strings.Count(text, axiomStr(args[0])))
			}
			return int64(0)
		case "read":
			return text
		}
	}
	switch typed := target.(type) {
	case []any:
		switch name {
		case "append":
			if len(args) > 0 {
				return append(typed, args[0])
			}
			return typed
		case "extend":
			if len(args) > 0 {
				return append(typed, axiomIter(args[0])...)
			}
			return typed
		case "pop":
			if len(typed) == 0 {
				return nil
			}
			return typed[len(typed)-1]
		case "index":
			if len(args) > 0 {
				for index, value := range typed {
					if axiomEq(value, args[0]) {
						return int64(index)
					}
				}
			}
			return int64(-1)
		case "count":
			count := int64(0)
			if len(args) > 0 {
				for _, value := range typed {
					if axiomEq(value, args[0]) {
						count++
					}
				}
			}
			return count
		case "copy":
			return append([]any{}, typed...)
		case "read":
			return typed
		}
	case map[string]any:
		switch name {
		case "get":
			fallback := any(nil)
			if len(args) > 1 {
				fallback = args[1]
			}
			if len(args) > 0 {
				if value, found := typed[axiomKey(args[0])]; found {
					return value
				}
			}
			return fallback
		case "keys":
			return axiomKeys(typed)
		case "values":
			return axiomValues(typed)
		case "items":
			return axiomItems(typed)
		case "update":
			if len(args) > 0 {
				if other, ok := args[0].(map[string]any); ok {
					for key, value := range other {
						typed[key] = value
					}
				}
			}
			return typed
		case "json":
			return typed["data"]
		case "copy":
			output := make(map[string]any, len(typed))
			for key, value := range typed {
				output[key] = value
			}
			return output
		}
	}
	reflected := reflect.ValueOf(target)
	if reflected.Kind() == reflect.Ptr {
		if reflected.IsNil() {
			return nil
		}
		reflected = reflected.Elem()
	}
	method := reflected.MethodByName(axiomFieldName(name))
	if !method.IsValid() {
		method = reflected.MethodByName(name)
	}
	if method.IsValid() {
		return axiomCallValue(method.Interface(), args...)
	}
	return nil
}

func axiomAppend(container any, value any) any {
	if items, ok := container.([]any); ok {
		return append(items, value)
	}
	return []any{container, value}
}

func axiomExtend(container any, values any) any {
	items, ok := container.([]any)
	if !ok {
		items = []any{container}
	}
	return append(items, axiomIter(values)...)
}

func axiomDict(pairs ...any) map[string]any {
	output := make(map[string]any)
	for index := 0; index+1 < len(pairs); index += 2 {
		output[axiomKey(pairs[index])] = pairs[index+1]
	}
	return output
}

func axiomMergeDict(left any, right any) map[string]any {
	output := make(map[string]any)
	if mapping, ok := left.(map[string]any); ok {
		for key, value := range mapping {
			output[key] = value
		}
	}
	if mapping, ok := right.(map[string]any); ok {
		for key, value := range mapping {
			output[key] = value
		}
	}
	return output
}

func axiomKeys(mapping map[string]any) []any {
	keys := make([]string, 0, len(mapping))
	for key := range mapping {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	output := make([]any, len(keys))
	for index, key := range keys {
		output[index] = key
	}
	return output
}

func axiomValues(mapping map[string]any) []any {
	keys := axiomKeys(mapping)
	output := make([]any, len(keys))
	for index, key := range keys {
		output[index] = mapping[axiomStr(key)]
	}
	return output
}

func axiomItems(mapping map[string]any) [][]any {
	keys := axiomKeys(mapping)
	output := make([][]any, len(keys))
	for index, key := range keys {
		output[index] = []any{key, mapping[axiomStr(key)]}
	}
	return output
}

func axiomConcat(values ...any) []any {
	output := []any{}
	for _, value := range values {
		output = append(output, axiomIter(value)...)
	}
	return output
}

func axiomSetFrom(values ...any) []any {
	output := []any{}
	for _, value := range values {
		if !axiomContains(output, value) {
			output = append(output, value)
		}
	}
	return output
}

func axiomRange(args ...any) []any {
	start, stop, step := int64(0), int64(0), int64(1)
	switch len(args) {
	case 1:
		stop = axiomInt(args[0])
	case 2:
		start, stop = axiomInt(args[0]), axiomInt(args[1])
	case 3:
		start, stop, step = axiomInt(args[0]), axiomInt(args[1]), axiomInt(args[2])
	}
	if step == 0 {
		step = 1
	}
	output := []any{}
	if step > 0 {
		for index := start; index < stop; index += step {
			output = append(output, index)
		}
	} else {
		for index := start; index > stop; index += step {
			output = append(output, index)
		}
	}
	return output
}

func axiomEnumerate(value any) [][]any {
	items := axiomIter(value)
	output := make([][]any, len(items))
	for index, item := range items {
		output[index] = []any{int64(index), item}
	}
	return output
}

func axiomZip(values ...any) [][]any {
	lists := make([][]any, len(values))
	size := -1
	for index, value := range values {
		lists[index] = axiomIter(value)
		if size < 0 || len(lists[index]) < size {
			size = len(lists[index])
		}
	}
	if size < 0 {
		return nil
	}
	output := make([][]any, size)
	for index := 0; index < size; index++ {
		row := make([]any, len(lists))
		for column, list := range lists {
			row[column] = list[index]
		}
		output[index] = row
	}
	return output
}

func axiomSum(value any) any {
	items := axiomIter(value)
	total := 0.0
	integral := true
	for _, item := range items {
		total += axiomFloat(item)
		if !axiomIsInt(item) {
			integral = false
		}
	}
	if integral {
		return int64(total)
	}
	return total
}

func axiomSorted(value any, reverse bool) []any {
	items := append([]any{}, axiomIter(value)...)
	sort.SliceStable(items, func(left, right int) bool {
		comparison := axiomCompare(items[left], items[right])
		if reverse {
			return comparison > 0
		}
		return comparison < 0
	})
	return items
}

func axiomReversed(value any) []any {
	items := append([]any{}, axiomIter(value)...)
	for left, right := 0, len(items)-1; left < right; left, right = left+1, right-1 {
		items[left], items[right] = items[right], items[left]
	}
	return items
}

func axiomMin(values ...any) any {
	items := values
	if len(values) == 1 {
		items = axiomIter(values[0])
	}
	if len(items) == 0 {
		return nil
	}
	result := items[0]
	for _, item := range items[1:] {
		if axiomCompare(item, result) < 0 {
			result = item
		}
	}
	return result
}

func axiomMax(values ...any) any {
	items := values
	if len(values) == 1 {
		items = axiomIter(values[0])
	}
	if len(items) == 0 {
		return nil
	}
	result := items[0]
	for _, item := range items[1:] {
		if axiomCompare(item, result) > 0 {
			result = item
		}
	}
	return result
}

func axiomAbs(value any) any {
	if axiomIsInt(value) {
		if axiomInt(value) < 0 {
			return -axiomInt(value)
		}
		return axiomInt(value)
	}
	return math.Abs(axiomFloat(value))
}

func axiomRound(value any) any {
	if axiomIsInt(value) {
		return axiomInt(value)
	}
	return math.Round(axiomFloat(value))
}

func axiomSlice(value any, start any, stop any, step any) any {
	items := axiomIter(value)
	length := len(items)
	from, to, stride := 0, length, 1
	if step != nil {
		if number, ok := axiomNumber(step); ok {
			stride = int(number)
		}
	}
	if stride == 0 {
		stride = 1
	}
	if stride > 0 {
		if start != nil {
			from, _ = axiomIndex(start)
			if from < 0 {
				from += length
			}
		}
		if stop != nil {
			to, _ = axiomIndex(stop)
			if to < 0 {
				to += length
			}
		}
	} else {
		from = length - 1
		to = -1
		if start != nil {
			from, _ = axiomIndex(start)
			if from < 0 {
				from += length
			}
		}
		if stop != nil {
			to, _ = axiomIndex(stop)
			if to < 0 {
				to += length
			}
		}
	}
	output := []any{}
	if stride > 0 {
		for index := from; index < to && index < length; index += stride {
			if index >= 0 {
				output = append(output, items[index])
			}
		}
	} else {
		for index := from; index > to && index >= 0; index += stride {
			if index < length {
				output = append(output, items[index])
			}
		}
	}
	if text, ok := value.(string); ok {
		var builder strings.Builder
		for _, item := range output {
			builder.WriteString(axiomStr(item))
		}
		return builder.String()
	}
	return output
}

func axiomJSONDumps(value any) string {
	encoded, err := json.Marshal(value)
	if err != nil {
		return ""
	}
	return string(encoded)
}

func axiomJSONLoads(value any) any {
	var decoded any
	if err := json.Unmarshal([]byte(axiomStr(value)), &decoded); err != nil {
		return nil
	}
	return decoded
}

func axiomHTTP(method string, url string, body any, options map[string]any) any {
	var reader io.Reader
	if body != nil {
		encoded, err := json.Marshal(body)
		if err == nil {
			reader = strings.NewReader(string(encoded))
		}
	}
	request, err := http.NewRequest(strings.ToUpper(method), url, reader)
	if err != nil {
		panic(axiomError(err))
	}
	if body != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	if options != nil {
		if headers, ok := options["headers"].(map[string]any); ok {
			for key, value := range headers {
				request.Header.Set(key, axiomStr(value))
			}
		}
	}
	client := &http.Client{}
	response, err := client.Do(request)
	if err != nil {
		panic(axiomError(err))
	}
	defer response.Body.Close()
	raw, _ := io.ReadAll(response.Body)
	var parsed any
	_ = json.Unmarshal(raw, &parsed)
	return map[string]any{
		"status_code": int64(response.StatusCode),
		"data":        parsed,
		"text":        string(raw),
		"ok":          response.StatusCode >= 200 && response.StatusCode < 300,
	}
}

func axiomReadFile(path any) any {
	content, err := os.ReadFile(axiomStr(path))
	if err != nil {
		panic(axiomError(err))
	}
	return string(content)
}

func axiomWriteFile(path any, content any) any {
	if err := os.WriteFile(axiomStr(path), []byte(axiomStr(content)), 0644); err != nil {
		panic(axiomError(err))
	}
	return nil
}

func axiomEnviron() map[string]any {
	output := make(map[string]any)
	for _, entry := range os.Environ() {
		parts := strings.SplitN(entry, "=", 2)
		if len(parts) == 2 {
			output[parts[0]] = parts[1]
		}
	}
	return output
}

func axiomTime() float64 {
	return float64(time.Now().UnixNano()) / 1e9
}

func axiomRandom() float64 {
	return rand.Float64()
}

func axiomRandint(low any, high any) int64 {
	minimum := axiomInt(low)
	maximum := axiomInt(high)
	if maximum <= minimum {
		return minimum
	}
	return minimum + rand.Int63n(maximum-minimum+1)
}

func axiomChoice(value any) any {
	items := axiomIter(value)
	if len(items) == 0 {
		return nil
	}
	return items[rand.Intn(len(items))]
}

func axiomType(value any) string {
	if value == nil {
		return "NoneType"
	}
	return reflect.TypeOf(value).String()
}

func axiomIsInstance(value any, names ...any) bool {
	if value == nil {
		for _, name := range names {
			if axiomStr(name) == "NoneType" {
				return true
			}
		}
		return false
	}
	reflected := reflect.TypeOf(value)
	for _, name := range names {
		target := axiomStr(name)
		switch target {
		case "str", "string":
			if reflected.Kind() == reflect.String {
				return true
			}
		case "int", "float", "number":
			if reflected.Kind() >= reflect.Int && reflected.Kind() <= reflect.Float64 {
				return true
			}
		case "bool", "boolean":
			if reflected.Kind() == reflect.Bool {
				return true
			}
		case "list", "tuple", "set", "dict", "mapping":
			if reflected.Kind() == reflect.Slice || reflected.Kind() == reflect.Map || reflected.Kind() == reflect.Array {
				return true
			}
		default:
			if reflected.String() == target || strings.HasSuffix(reflected.String(), "."+target) {
				return true
			}
		}
	}
	return false
}

func axiomError(value any) any {
	if err, ok := value.(error); ok {
		return err
	}
	return fmt.Errorf("%v", value)
}

func axiomNamed(target *any, value any) any {
	*target = value
	return value
}

func axiomToSet(value any) []any {
	return axiomSetFrom(axiomIter(value)...)
}

func axiomToDict(value any) map[string]any {
	if mapping, ok := value.(map[string]any); ok {
		return axiomMergeDict(mapping, nil)
	}
	output := make(map[string]any)
	for _, item := range axiomIter(value) {
		pair := axiomIter(item)
		if len(pair) >= 2 {
			output[axiomKey(pair[0])] = pair[1]
		}
	}
	return output
}

func axiomMap(function any, value any) []any {
	items := axiomIter(value)
	output := make([]any, 0, len(items))
	for _, item := range items {
		output = append(output, axiomCallValue(function, item))
	}
	return output
}

func axiomFilter(function any, value any) []any {
	items := axiomIter(value)
	output := []any{}
	for _, item := range items {
		if axiomTruthy(axiomCallValue(function, item)) {
			output = append(output, item)
		}
	}
	return output
}

func axiomAny(value any) bool {
	for _, item := range axiomIter(value) {
		if axiomTruthy(item) {
			return true
		}
	}
	return false
}

func axiomAll(value any) bool {
	for _, item := range axiomIter(value) {
		if !axiomTruthy(item) {
			return false
		}
	}
	return true
}

func axiomCallable(value any) bool {
	if value == nil {
		return false
	}
	return reflect.ValueOf(value).Kind() == reflect.Func
}

func axiomSleep(seconds any) any {
	time.Sleep(time.Duration(axiomFloat(seconds) * float64(time.Second)))
	return nil
}

func axiomGetenv(key any, fallback any) any {
	value, found := os.LookupEnv(axiomStr(key))
	if !found {
		return fallback
	}
	return value
}

func axiomPathJoin(values ...any) string {
	parts := make([]string, 0, len(values))
	for _, value := range values {
		parts = append(parts, axiomStr(value))
	}
	return strings.Join(parts, "/")
}

func axiomMkdirs(path any) any {
	if err := os.MkdirAll(axiomStr(path), 0755); err != nil {
		panic(axiomError(err))
	}
	return nil
}
"""
