# Read-only effective-property, display-property and logical-pin audit.
proc auditCell {v} { return [string map [list "\t" {\t} "\n" {\n} "\r" {}] $v] }
proc auditRow {kind key name value} {
    global attributeReport
    puts $attributeReport "[auditCell $kind]\t[auditCell $key]\t[auditCell $name]\t[auditCell $value]"
}
proc auditProps {obj key} {
    set state [DboState]
    if {[catch {
        set iter [$obj NewEffectivePropsIter $state]
        set name [DboTclHelper_sMakeCString]
        set val [DboTclHelper_sMakeCString]
        set typ [DboTclHelper_sMakeDboValueType]
        set edit [DboTclHelper_sMakeInt]
        set result [$iter NextEffectiveProp $name $val $typ $edit]
        while {[$result OK] == 1} {
            auditRow PROPERTY $key [DboTclHelper_sGetConstCharPtr $name] [DboTclHelper_sGetConstCharPtr $val]
            set result [$iter NextEffectiveProp $name $val $typ $edit]
        }
        delete_DboEffectivePropsIter $iter
    } err]} { auditRow ERROR $key effective_properties $err }
    if {[catch {
        set iter [$obj NewDisplayPropsIter $state]
        set prop [$iter NextProp $state]
        while {$prop != "NULL"} {
            set name [DboTclHelper_sMakeCString]
            $prop GetName $name
            set pt [$prop GetLocation $state]
            set loc [list [DboTclHelper_sGetCPointX $pt] [DboTclHelper_sGetCPointY $pt]]
            auditRow DISPLAY $key [DboTclHelper_sGetConstCharPtr $name] [list type [$prop GetDisplayType $state] xy $loc rotation [$prop GetRotation $state]]
            set prop [$iter NextProp $state]
        }
        delete_DboDisplayPropsIter $iter
    } err]} { auditRow ERROR $key display_properties $err }
}
proc auditPin {pin key status} {
    set pinName [DboTclHelper_sMakeCString]
    $pin GetPinName $pinName
    auditRow PIN $key GetPinName [DboTclHelper_sGetConstCharPtr $pinName]
    foreach method {GetPinType GetIsVisible GetIsNumberVisible GetIsGlobal GetIsLong GetIsClock GetIsDot GetIsLeftPointing GetIsRightPointing GetIsNetStyle GetIsNoConnect GetIsUserDefinedShape} {
        if {[catch {$pin $method $status} result]} { auditRow ERROR $key $method $result } else { auditRow PIN $key $method $result }
    }
    foreach method {GetStartPoint GetHotSpot} {
        if {[catch {
            set pt [$pin $method $status]
            set result [list [DboTclHelper_sGetCPointX $pt] [DboTclHelper_sGetCPointY $pt]]
        } err]} { auditRow ERROR $key $method $err } else { auditRow PIN $key $method $result }
    }
}
